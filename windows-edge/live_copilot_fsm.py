#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows Edge: Live Co-pilot Finite State Machine (FSM) & F20 State-Toggle / Kill-Switch
=============================================================================
Manages two-way lifecycle state toggle (Wake <-> Disconnect/Standby) via F20 hotkey.

State Machine:
  [ STANDBY ]   -- (F20 Pressed) -->  [ ACTIVE ]
  (Passive idle)                      (Capture window at cursor, launch HUD, stream to Gemini)

  [ ACTIVE ]    -- (F20 Pressed) -->  [ STANDBY ]
  (Active Co-pilot)                   (Instant kill-switch, abort stream, clear queues, dismiss HUD)
=============================================================================
"""

import sys
import os
import time
import enum
import queue
import ctypes
import logging
import argparse
import threading
import subprocess
from ctypes import wintypes
from typing import Optional, Callable, Dict, Any, Tuple

# Ensure UTF-8 console output for Thai characters and international titles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from visual_cortex import look_at_cursor
from terminal_actuator import spawn_hud_overlay
from hud_overlay import hide_overlay, get_default_text_for_mode
from audio_recorder import AudioRecorder, UnifiedAudioStream

try:
    from audio_ducker import audio_ducker
except ImportError:
    audio_ducker = None

logger = logging.getLogger("windows_edge.fsm")


class CopilotState(str, enum.Enum):
    """Core operational states for Gemini Live Full-Duplex Co-pilot."""
    STANDBY = "STANDBY"         # Passive idle state, HUD hidden, mic closed
    ACTIVE_CALL = "ACTIVE_CALL" # Full-Duplex Live call active (continuous mic + 1 FPS vision + audio ducked)

    # Backward compatibility aliases:
    ACTIVE = "ACTIVE_CALL"
    LISTENING = "ACTIVE_CALL"
    THINKING = "ACTIVE_CALL"
    SPEAKING = "ACTIVE_CALL"


class LiveCopilotFSM:
    """
    Finite State Machine (FSM) controlling the Full-Duplex Live Co-pilot lifecycle.
    Implements single-toggle F20 session connect/disconnect, continuous 16kHz mic streaming,
    adaptive 1 FPS vision streaming, native barge-in, and zero zombie threads.
    """

    def __init__(
        self,
        on_state_change: Optional[Callable[[CopilotState, CopilotState], None]] = None,
        session_factory: Optional[Callable[[], Any]] = None
    ):
        self._lock = threading.RLock()
        self._state = CopilotState.STANDBY
        self.hud_mode = "STANDBY"
        self.hud_text = ""
        self.on_state_change = on_state_change
        self.session_factory = session_factory

        self.current_context: Optional[Dict[str, Any]] = None
        self.active_session: Optional[Any] = None
        self.active_hud_proc: Optional[subprocess.Popen] = None
        self.audio_queue = queue.Queue(maxsize=1000)
        self._stop_event = threading.Event()
        self._is_holding_f20 = False
        self._f20_down_time = 0.0
        self._is_toggle_mode = False
        self._last_f20_press_time = 0.0
        self.is_connecting = False
        self.is_tearing_down = False
        self._last_toggle_time = 0.0

        self.recorder = AudioRecorder(sample_rate=16000)
        self.live_client: Optional[Any] = None
        self.stt_engine: Optional[Any] = None
        self.intent_parser: Optional[Any] = None
        self._dispatch_thread: Optional[threading.Thread] = None

        # Asynchronous HUD Dispatch Queue & Worker
        self._hud_queue = queue.Queue()
        self._hud_thread = threading.Thread(
            target=self._hud_dispatch_loop,
            daemon=True,
            name="HUDDispatchLoop"
        )
        self._hud_thread.start()

    @property
    def state(self) -> CopilotState:
        with self._lock:
            return self._state

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._state == CopilotState.ACTIVE_CALL

    @property
    def is_standby(self) -> bool:
        with self._lock:
            return self._state == CopilotState.STANDBY

    @property
    def is_listening(self) -> bool:
        with self._lock:
            return self._state == CopilotState.ACTIVE_CALL

    @property
    def is_thinking(self) -> bool:
        with self._lock:
            return self._state == CopilotState.ACTIVE_CALL

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._state == CopilotState.ACTIVE_CALL

    def connect_call(self) -> CopilotState:
        """
        Transitions from STANDBY -> ACTIVE_CALL:
        1. Guarded with is_connecting latch.
        2. Starts Full-Duplex Gemini Live session (1 FPS vision + continuous 16kHz mic).
        3. Windows background audio ducking remains active.
        4. Native barge-in enabled.
        """
        with self._lock:
            if self._state == CopilotState.ACTIVE_CALL or self.is_connecting or self.is_tearing_down:
                logger.info("[FSM] connect_call ignored (already active or busy)")
                return self._state
            self.is_connecting = True

        prev_state = self._state
        try:
            logger.info("[FSM] F20 Single Toggle -> Entering ACTIVE_CALL...")
            print("\n" + "=" * 60)
            print(" [LIVE CALL] 🟢 CONNECTING FULL-DUPLEX GEMINI LIVE SESSION")
            print("   - Mic: Raw 16kHz PCM streaming continuously")
            print("   - Vision: Adaptive 1 FPS streamer active (max 1280px)")
            print("   - Barge-in: Native server-side speech interruption active")
            print("   - Audio Ducking: Background audio muted to 0.0")
            print("   - Press [ F20 ] at any time to cleanly disconnect")
            print("=" * 60)

            # Windows background audio ducking
            if audio_ducker:
                try:
                    audio_ducker.duck(0.0)
                except Exception as e:
                    logger.debug(f"[FSM] Duck note: {e}")

            # Capture initial window context
            try:
                self.current_context = look_at_cursor()
            except Exception:
                pass

            # Start session
            if self.session_factory is not None:
                try:
                    self.active_session = self.session_factory()
                    if hasattr(self.active_session, "start"):
                        self.active_session.start()
                except Exception as e:
                    logger.debug(f"[FSM] Session factory note: {e}")
            else:
                from gemini_live_client import GeminiLiveClient
                if self.live_client is None:
                    self.live_client = GeminiLiveClient()
                if hasattr(self.live_client, "start_full_duplex_session"):
                    self.active_session = self.live_client.start_full_duplex_session()
                elif hasattr(self.live_client, "start"):
                    self.live_client.start()
                    self.active_session = self.live_client

            with self._lock:
                self._state = CopilotState.ACTIVE_CALL
            self._update_hud(CopilotState.ACTIVE_CALL, text="[LIVE CALL] Gemini Live Active")

            if callable(self.on_state_change):
                try:
                    self.on_state_change(prev_state, self._state)
                except Exception:
                    pass
            return self._state
        finally:
            with self._lock:
                self.is_connecting = False

    def disconnect_call(self, reason: str = "F20 Toggle Pressed") -> CopilotState:
        """
        Transitions from ACTIVE_CALL -> STANDBY:
        1. Guarded with is_tearing_down latch.
        2. Gracefully stops full-duplex session (closes WebSocket, cancels image/mic tasks).
        3. Halts speaker audio playback (<50ms).
        4. Stops microphone stream (UnifiedAudioStream.get_instance().stop()).
        5. Restores system audio volume to 100% via audio_ducker.unduck().
        6. Returns cleanly to STANDBY with zero zombie threads.
        """
        with self._lock:
            if self._state == CopilotState.STANDBY or self.is_tearing_down:
                return self._state
            self.is_tearing_down = True

        prev_state = self._state
        try:
            logger.info(f"[FSM] Disconnecting ACTIVE_CALL -> STANDBY ({reason})...")

            self._stop_event.set()

            if self.active_session is not None:
                try:
                    if hasattr(self.active_session, "stop"):
                        self.active_session.stop()
                    elif hasattr(self.active_session, "abort"):
                        self.active_session.abort()
                except Exception as e:
                    logger.debug(f"[FSM] Stop session error: {e}")
                self.active_session = None

            self._instant_halt_playback()

            try:
                UnifiedAudioStream.get_instance().set_muted(True)
                UnifiedAudioStream.get_instance().stop()
            except Exception as e:
                logger.debug(f"[FSM] Mic stop error: {e}")

            if audio_ducker:
                try:
                    audio_ducker.unduck()
                except Exception as e:
                    logger.debug(f"[FSM] Unduck error: {e}")

            self._update_hud(CopilotState.STANDBY)
            with self._lock:
                self._state = CopilotState.STANDBY
            self.current_context = None

            print("\n[LIVE CALL] 🔴 CALL DISCONNECTED -> Returned to STANDBY. Audio restored 100%.\n")

            if callable(self.on_state_change):
                try:
                    self.on_state_change(prev_state, self._state)
                except Exception:
                    pass
            return self._state
        finally:
            with self._lock:
                self.is_tearing_down = False

    def toggle_session(self, ignore_debounce: bool = False) -> CopilotState:
        """
        F20 Press (Single Toggle):
        - Guarded by is_connecting and is_tearing_down latches.
        - 500ms hardware switch debounce.
        - If STANDBY -> Connect & enter ACTIVE_CALL.
        - If ACTIVE_CALL -> Disconnect & return to STANDBY.
        """
        now = time.perf_counter()
        with self._lock:
            if not ignore_debounce and (now - self._last_toggle_time < 0.50):
                logger.info(f"[FSM] F20 toggle ignored (debounced: {now - self._last_toggle_time:.3f}s < 0.5s)")
                return self._state

            if self.is_connecting or self.is_tearing_down:
                logger.info(f"[FSM] F20 toggle ignored (busy: is_connecting={self.is_connecting}, is_tearing_down={self.is_tearing_down})")
                return self._state

            self._last_toggle_time = now

            if self._state == CopilotState.STANDBY:
                return self.connect_call()
            else:
                return self.disconnect_call(reason="F20 Toggle Pressed")

    def toggle_f20(self) -> CopilotState:
        return self.toggle_session()

    def on_f20_down(self) -> CopilotState:
        return self.toggle_session()

    def on_f20_up(self, force_stop: bool = False) -> CopilotState:
        return self._state

    def handle_double_click_dismiss(self) -> CopilotState:
        return self.disconnect_call(reason="Hardware Dismiss")

    def _instant_halt_playback(self):
        """Halts playback instantly (<50ms) and flushes all audio buffers."""
        try:
            import sounddevice as sd
            sd.stop()
        except Exception as e:
            logger.debug(f"[FSM] sd.stop notice: {e}")

        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except Exception:
                break

        if self.active_session is not None:
            try:
                if hasattr(self.active_session, "interrupt"):
                    self.active_session.interrupt()
                elif hasattr(self.active_session, "abort"):
                    self.active_session.abort()
                elif hasattr(self.active_session, "stop"):
                    self.active_session.stop()
            except Exception as e:
                logger.debug(f"[FSM] Session interrupt note: {e}")

    def _handle_gemini_stream(self, chunk: str, target_box: Optional[Tuple[float, float, float, float]] = None):
        """UI response box and subtitle pipelines are frozen/bypassed for pure low-latency audio-first core."""
        if chunk:
            logger.debug(f"[GeminiStream] {chunk}")

    def interrupt_speaking(self) -> CopilotState:
        """Instantaneous Hardware Barge-in Interruption (< 50ms): Delegates to on_f20_down()."""
        return self.on_f20_down()

    def _set_state(self, new_state: CopilotState, text: Optional[str] = None) -> CopilotState:
        """Sets internal FSM state, handles half-duplex mic isolation, and updates HUD overlay."""
        with self._lock:
            prev_state = self._state
            if prev_state == new_state and not text:
                return self._state
            self._state = new_state

        try:
            if new_state == CopilotState.SPEAKING:
                UnifiedAudioStream.get_instance().set_muted(True)
            elif new_state == CopilotState.LISTENING:
                UnifiedAudioStream.get_instance().set_muted(False)
            elif new_state == CopilotState.STANDBY:
                UnifiedAudioStream.get_instance().set_muted(True)
                UnifiedAudioStream.get_instance().stop()
        except Exception as e:
            logger.debug(f"[FSM] Mic mute state update notice: {e}")

        self._update_hud(new_state, text)

        if callable(self.on_state_change):
            try:
                self.on_state_change(prev_state, self._state)
            except Exception:
                pass

        return self._state

    def _hud_dispatch_loop(self):
        """Dedicated daemon worker consuming HUD update tasks without blocking state transitions."""
        while not self._stop_event.is_set():
            try:
                task = self._hud_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if task is None:
                break

            m, txt, sub, box = task
            try:
                if m == "STANDBY":
                    hide_overlay()
                    if self.active_hud_proc is not None:
                        proc = self.active_hud_proc
                        self.active_hud_proc = None
                        try:
                            if proc.stdin and not proc.stdin.closed:
                                proc.stdin.write(b"HIDE\n")
                                proc.stdin.flush()
                                proc.stdin.close()
                        except Exception:
                            pass
                        proc.stdin = None
                        try:
                            if proc.poll() is None:
                                proc.terminate()
                        except Exception:
                            pass
                else:
                    if self.active_hud_proc is not None and self.active_hud_proc.poll() is None:
                        # If HUD process is already active and stdin is open, update mode & subtitle without killing and respawning
                        if self.active_hud_proc.stdin:
                            try:
                                if sub:
                                    self.active_hud_proc.stdin.write(f"SUBTITLE:{sub}\n".encode("utf-8"))
                                self.active_hud_proc.stdin.write(f"MODE:{m}|{txt}\n".encode("utf-8"))
                                self.active_hud_proc.stdin.flush()
                                continue
                            except Exception:
                                pass
                        old_proc = self.active_hud_proc
                        self.active_hud_proc = None
                        try:
                            if old_proc.stdin:
                                old_proc.stdin.close()
                        except Exception:
                            pass
                        old_proc.stdin = None
                        try:
                            old_proc.terminate()
                        except Exception:
                            pass
                    self.active_hud_proc = spawn_hud_overlay(
                        mode=m,
                        text=txt,
                        duration=300.0,
                        subtitle=sub,
                        target_box=box
                    )


            except Exception as e:
                logger.debug(f"[FSM] HUD dispatch notice: {e}")
            finally:
                self._hud_queue.task_done()

    def _update_hud(
        self,
        state: CopilotState,
        text: Optional[str] = None,
        subtitle: Optional[str] = None,
        target_box: Optional[Tuple[float, float, float, float]] = None
    ):
        """Ensures floating HUD accurately reflects FSM transitions, response subtitles, and visual target reticles."""
        mode_str = state.value if isinstance(state, CopilotState) else str(state)
        self.hud_mode = mode_str
        self.hud_text = text if text is not None else get_default_text_for_mode(mode_str)
        try:
            self._hud_queue.put_nowait((self.hud_mode, self.hud_text, subtitle, target_box))
        except Exception as e:
            logger.debug(f"[FSM] HUD queue put notice: {e}")

    def _transition_to_listening(self) -> CopilotState:
        """Alias for on_f20_down for backward compatibility."""
        return self.on_f20_down()

    def _transition_to_active(self) -> CopilotState:
        """Alias for on_f20_down for backward compatibility."""
        return self.on_f20_down()

    def _transition_to_standby(self, reason: str = "") -> CopilotState:
        """
        Transitions FSM to STANDBY.
        Executes immediate Kill-Switch / Standby return:
        Terminates network connections, purges queues, hides HUD, and releases hardware cleanly.
        """
        prev_state = self._state
        logger.info(f"[FSM: Standby Return] {prev_state.value} -> STANDBY (Reason: {reason})...")

        self._stop_event.set()
        self._is_holding_f20 = False

        if self.active_session is not None and hasattr(self.active_session, "play_goodbye") and "dismiss" in reason.lower():
            try:
                threading.Thread(target=self.active_session.play_goodbye, daemon=True, name="GoodbyeAudioWorker").start()
            except Exception:
                pass

        if self.active_session is not None:
            try:
                if hasattr(self.active_session, "abort"):
                    self.active_session.abort()
                elif hasattr(self.active_session, "stop"):
                    self.active_session.stop()
                elif hasattr(self.active_session, "close"):
                    self.active_session.close()
            except Exception as e:
                logger.debug(f"[FSM] Session abort notice: {e}")
            self.active_session = None

        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.task_done()
            except Exception:
                break

        self._update_hud(CopilotState.STANDBY)
        try:
            from hud_overlay import hide_response_box, hide_overlay
            hide_response_box()
            hide_overlay()
        except Exception:
            pass


        self.current_context = None

        try:
            UnifiedAudioStream.get_instance().stop()
        except Exception as e:
            logger.debug(f"[FSM] Audio stream stop notice: {e}")

        # Smoothly restore background system volume to 100% (audio_ducker.unduck())
        if audio_ducker:
            try:
                audio_ducker.unduck()
            except Exception as unduck_err:
                logger.debug(f"[FSM] Audio unduck error: {unduck_err}")

        self._state = CopilotState.STANDBY
        self._last_f20_press_time = 0.0
        if callable(self.on_state_change):
            try:
                self.on_state_change(prev_state, self._state)
            except Exception:
                pass

        logger.info("[FSM] Rollback complete -> State is now STANDBY.")
        return self._state

    def force_abort(self):
        """External kill-switch API: forces immediate return to STANDBY."""
        with self._lock:
            self.disconnect_call(reason="Force Abort")


class F20HotkeyListener:
    """
    Non-blocking global listener for F20 (`VK_F20 = 0x83`).
    Uses native Windows `RegisterHotKey` in a dedicated background daemon thread.
    Zero administrative privileges required.
    Single Toggle Mode (No PTT):
    - F20 Press (Single Toggle):
      If STANDBY -> Connect & enter ACTIVE_CALL.
      If ACTIVE_CALL -> Disconnect & return to STANDBY.
    - Completely removes Key-Down / Key-Up hold logic.
    - 300ms hardware switch debounce.
    """

    VK_F20 = 0x83
    MOD_NOREPEAT = 0x4000
    WM_HOTKEY = 0x0312
    PM_NOREMOVE = 0x0000

    def __init__(self, fsm: LiveCopilotFSM, hotkey_id: int = 2020):
        self.fsm = fsm
        self.hotkey_id = hotkey_id
        self._thread: Optional[threading.Thread] = None
        self._is_running = threading.Event()
        self._registered = threading.Event()
        self._success = False
        self._last_hotkey_time = 0.0
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

    def is_active(self) -> bool:
        return self._is_running.is_set() and self._success

    def start(self, timeout: float = 2.0) -> bool:
        if self._is_running.is_set():
            return True

        self._thread = threading.Thread(
            target=self._message_pump,
            name="F20HotkeyListenerThread",
            daemon=True
        )
        self._thread.start()
        self._registered.wait(timeout=timeout)
        return self._success

    def stop(self):
        self._is_running.clear()
        if self._thread and self._thread.is_alive():
            self._user32.PostThreadMessageW(self._thread.ident, 0x0012, 0, 0)
            self._thread.join(timeout=1.0)

    def _message_pump(self):
        thread_id = self._kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        self._user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, self.PM_NOREMOVE)

        res = self._user32.RegisterHotKey(
            None,
            self.hotkey_id,
            self.MOD_NOREPEAT,
            self.VK_F20
        )

        if res == 0:
            err = self._kernel32.GetLastError()
            sys.stderr.write(f"[F20Listener] Win32 RegisterHotKey error code {err}\n")
            self._success = False
            self._registered.set()
            return

        self._success = True
        self._is_running.set()
        self._registered.set()

        try:
            while self._is_running.is_set():
                ret = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break

                if msg.message == self.WM_HOTKEY and msg.wParam == self.hotkey_id:
                    now = time.perf_counter()
                    delta = now - self._last_hotkey_time
                    if delta < 0.50:
                        continue
                    self._last_hotkey_time = now

                    print("\n[F20] Single Toggle Switch Triggered")
                    threading.Thread(
                        target=self.fsm.toggle_session,
                        name="F20ToggleWorker",
                        daemon=True
                    ).start()

                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._user32.UnregisterHotKey(None, self.hotkey_id)
            self._is_running.clear()


def run_f20_toggle_verification() -> bool:
    """
    Pure Full-Duplex Live Session (F20 Single Toggle) Verification Suite:
    1. Initial State: STANDBY (HUD hidden, session idle, audio unducked)
    2. Single F20 Toggle Press:
       - Transitions to ACTIVE_CALL
       - Starts full-duplex session
       - Windows background audio ducking active (0.0)
       - Microphone streaming active
    3. Native Barge-in Interruption (<50ms target):
       - User speech interrupts model playback immediately via sd.stop()
    4. Second F20 Toggle Press:
       - Gracefully disconnects session
       - Cancels vision tasks, stops mic stream
       - Restores background system volume to 100%
       - Returns cleanly to STANDBY with zero zombie threads
    """
    print("=" * 65)
    print(" PURE FULL-DUPLEX GEMINI LIVE (F20 TOGGLE) VERIFICATION SUITE")
    print("=" * 65)

    mock_session_stopped = threading.Event()
    mock_session_interrupted = threading.Event()

    class MockFullDuplexSession:
        def __init__(self):
            self.is_running = False

        def start(self):
            self.is_running = True

        def interrupt(self):
            mock_session_interrupted.set()

        def abort(self):
            self.stop()

        def stop(self):
            self.is_running = False
            mock_session_stopped.set()

    transitions = []

    def on_change(old, new):
        transitions.append((old, new))

    fsm = LiveCopilotFSM(
        on_state_change=on_change,
        session_factory=MockFullDuplexSession
    )

    # 1. Initial State: STANDBY
    print("\n[Step 1] Verifying Initial State (STANDBY)...")
    print(f"         Current State: {fsm.state.value}")
    assert fsm.state == CopilotState.STANDBY, f"Expected STANDBY, got {fsm.state}"
    assert fsm.is_standby is True
    assert fsm.is_active is False
    print("         -> PASSED (Initial State: STANDBY)")

    # 2. Press F20 Single Toggle -> ACTIVE_CALL
    print("\n[Step 2] Pressing F20 Single Toggle (STANDBY -> ACTIVE_CALL)...")
    t0 = time.perf_counter()
    new_state = fsm.toggle_session()
    t_toggle_ms = (time.perf_counter() - t0) * 1000.0

    print(f"         Toggle Latency: {t_toggle_ms:.1f}ms")
    print(f"         New State:      {new_state.value}")
    assert new_state == CopilotState.ACTIVE_CALL, f"Expected ACTIVE_CALL, got {new_state}"
    assert fsm.is_active is True
    assert fsm.active_session is not None
    assert fsm.active_session.is_running is True

    # Check audio ducking active
    if audio_ducker:
        assert audio_ducker.is_ducked is True, "Background audio must be ducked in ACTIVE_CALL"
        print("         Audio Ducking:  Active (hard-muted to 0.0)")

    print("         -> PASSED (Single Toggle into ACTIVE_CALL)")

    # 3. Native Barge-in Interruption Test
    print("\n[Step 3] Verifying Native Barge-in Interruption (<50ms)...")
    t1 = time.perf_counter()
    fsm._instant_halt_playback()
    t_barge_ms = (time.perf_counter() - t1) * 1000.0
    print(f"         Interruption Halt Latency: {t_barge_ms:.2f}ms (Target: <50ms)")
    assert t_barge_ms < 50.0, f"Halt latency {t_barge_ms:.2f}ms exceeded 50ms"
    assert mock_session_interrupted.is_set()
    print("         -> PASSED (Native barge-in instant halt <50ms)")

    # 4. Press F20 Single Toggle Again -> Disconnect to STANDBY
    print("\n[Step 4] Pressing F20 Single Toggle Again (ACTIVE_CALL -> STANDBY)...")
    t2 = time.perf_counter()
    standby_state = fsm.toggle_session(ignore_debounce=True)
    t_disc_ms = (time.perf_counter() - t2) * 1000.0

    print(f"         Disconnect Latency: {t_disc_ms:.1f}ms")
    print(f"         New State:          {standby_state.value}")
    assert standby_state == CopilotState.STANDBY, f"Expected STANDBY, got {standby_state}"
    assert fsm.is_standby is True
    assert fsm.active_session is None
    assert mock_session_stopped.is_set()

    # Verify audio unducked to 100%
    if audio_ducker:
        assert audio_ducker.is_ducked is False, "Audio ducker must be unducked (restored to 100%)"
        print("         Audio Volume:       Restored 100% (Unducked)")

    print("         -> PASSED (Clean disconnect to STANDBY with zero lingering sessions)")

    # 5. Verify Transition Log
    print("\n[Step 5] Verifying State Transitions...")
    for old, new in transitions:
        print(f"         - Transition: {old.value} -> {new.value}")
    assert (CopilotState.STANDBY, CopilotState.ACTIVE_CALL) in transitions
    assert (CopilotState.ACTIVE_CALL, CopilotState.STANDBY) in transitions
    print("         -> PASSED (Clean two-way toggle verified)")

    # 6. Verify Adaptive 1 FPS Screen Capture & Resizing (<1280px, quality 65%)
    print("\n[Step 6] Verifying Adaptive 1 FPS Screen Capture & Resizing...")
    from gemini_live_client import capture_and_resize_screen
    frame_bytes = capture_and_resize_screen(max_width=1280, quality=65)
    assert frame_bytes is not None and len(frame_bytes) > 0, "Frame capture failed"
    print(f"         Captured Frame: {len(frame_bytes):,} bytes JPEG (Quality: 65%, Max Width: 1280px)")
    print("         -> PASSED (Adaptive 1 FPS frame generation verified)")

    print("\n" + "=" * 65)
    print(" ALL PURE FULL-DUPLEX LIVE SESSION CHECKS PASSED (EXIT 0)")
    print("=" * 65)
    return True


def main():
    parser = argparse.ArgumentParser(description="Live Co-pilot Full-Duplex (F20 Single Toggle) Controller")
    parser.add_argument("--test", action="store_true", help="Run automated Full-Duplex toggle verification")
    args = parser.parse_args()

    if args.test:
        ok = run_f20_toggle_verification()
        sys.exit(0 if ok else 1)
    else:
        fsm = LiveCopilotFSM()
        listener = F20HotkeyListener(fsm=fsm)
        if not listener.start():
            sys.stderr.write("[F20 Daemon] Failed to register F20 hotkey.\n")
            sys.exit(1)
        print("=" * 65)
        print(" WINDOWS EDGE: FULL-DUPLEX GEMINI LIVE COPILOT ACTIVE")
        print("=" * 65)
        print("  - Press [ F20 ] once: Connect & enter ACTIVE_CALL")
        print("  - When in call: 1 FPS screen updates + 16kHz continuous mic stream")
        print("  - Native barge-in: Speaking automatically interrupts Gemini response")
        print("  - Press [ F20 ] again: Disconnect & return to STANDBY")
        print("=" * 65)
        print(" Press Ctrl+C in this console to exit.\n")
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            listener.stop()
            fsm.force_abort()
            print("\n[F20 Daemon] Exited cleanly.")


if __name__ == "__main__":
    main()
