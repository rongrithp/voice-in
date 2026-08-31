import io
import queue
import threading
import time
import logging
import winsound
import numpy as np
import keyboard
from pynput import keyboard as pynput_keyboard

import config
from src.sanitizer import sanitize_text, reset_dedup_memory, DeltaTextTracker
from src.router import TranscribeEngine
from src.vad import WebRTCVADSegmenter
from src.actuator import inject_to_cursor, inject_delta_text, copy_cursor_to_bottom, copy_selected_text, TextActuator, paste_text, StreamingTextInjector
from src.audio import calculate_rms, is_silence, robust_audio_stream_capture, LiveAudioStreamProducer
from src import audio_control
from src.tts_engine import TTSEngine, split_text_chunks
from src.audio_player import AudioPlayer, PlaybackState
from src.tray_manager import TrayManager, DaemonStatus
from src.screen_capture import capture_monitor_to_clipboard
from src.usage_tracker import UsageTracker
from src.live_copilot import LiveCopilotSession
from src.gui_dashboard import DashboardGUI
from src.windows_local_tts import WindowsNativeTTSEngine
from src.hud_overlay import HUDOverlay, HUDState

logger = logging.getLogger("VoiceOperatingHub")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


class HotkeyActionHandler:
    """
    Handles single-click vs double-click timing logic (< 300ms window) with key repeat / debounce suppression.
    Dispatches on_single_click after threshold if no second press occurs,
    or on_double_click immediately on fast double press.
    """

    def __init__(self, on_single_click, on_double_click, threshold: float = 0.30, debounce_interval: float = 0.08):
        self.on_single_click = on_single_click
        self.on_double_click = on_double_click
        self.threshold = threshold
        self.debounce_interval = debounce_interval
        self.last_press_time = 0.0
        self.is_down = False
        self.timer: threading.Timer = None
        self.lock = threading.Lock()

    def handle_press(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_press_time

            # 1. Suppress Windows key repeat events when key is held down
            if self.is_down:
                return

            # 2. Suppress hardware bounce / rapid micro-triggers (< debounce_interval)
            if self.last_press_time > 0 and elapsed < self.debounce_interval:
                return

            self.is_down = True

            # 3. Check for double-click within threshold window
            if self.timer is not None and elapsed < self.threshold:
                # Double click detected: cancel single-click timer and fire double-click immediately
                self.timer.cancel()
                self.timer = None
                self.last_press_time = 0.0
                threading.Thread(target=self.on_double_click, daemon=True).start()
            else:
                # First press: record timestamp and start single-click timer
                self.last_press_time = now
                if self.timer is not None:
                    self.timer.cancel()
                self.timer = threading.Timer(self.threshold, self._trigger_single_click)
                self.timer.daemon = True
                self.timer.start()

    def handle_release(self):
        with self.lock:
            self.is_down = False

    def _trigger_single_click(self):
        with self.lock:
            if self.timer is None:
                return
            self.timer = None
            self.last_press_time = 0.0
        try:
            self.on_single_click()
        except Exception as e:
            logger.error(f"[HotkeyActionHandler] Error in on_single_click: {e}")

    def cancel(self):
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            self.last_press_time = 0.0
            self.is_down = False


class VoiceOperatingHubApp:
    """
    Two-Way Voice Operating Hub Daemon.
    Integrates Speak-to-Cursor (F21 STT) & Read-to-Ear (F22 TTS) pipelines
    with OS Master Audio Control and System Tray Manager.
    """

    def __init__(self, start_gui: bool = True):
        self.lock = threading.Lock()
        self._toggle_lock = threading.Lock()  # Re-entrant / concurrency guard for STT toggle
        self._stop_event = threading.Event()
        self._warmup_event = threading.Event()
        self.is_streaming = False       # Pipeline A (STT) active state
        self.is_running = True          # Daemon running flag
        self.session_audio_frames = []  # Rolling Audio Accumulator frames

        # Fast In-Memory Pipelines & Handlers (Pure in-memory, instant < 5ms)
        self.audio_queue = queue.Queue(maxsize=config.MAX_QUEUE_SIZE)
        self.engine = TranscribeEngine()
        self.active_live_session = None
        self.vad_segmenter = WebRTCVADSegmenter()
        self.delta_tracker = DeltaTextTracker()
        self.actuator = TextActuator()
        self.stream_injector = StreamingTextInjector()
        self.tts_engine = TTSEngine()
        initial_tts_speed = float(getattr(config, "TTS_SPEAKING_RATE", 1.0))
        initial_tts_voice = getattr(config, "TTS_VOICE", "th-TH-Neural2-C")
        self.local_tts_engine = WindowsNativeTTSEngine(speed=initial_tts_speed)
        self._current_tts_session = 0.0
        self._audio_player: Optional[AudioPlayer] = None
        self._live_audio_producer: Optional[LiveAudioStreamProducer] = None

        self.usage_tracker = UsageTracker()
        self.live_copilot = LiveCopilotSession()
        self._stt_start_time = 0.0

        # Log detected monitors with resolutions at startup
        try:
            from src.screen_capture import log_detected_monitors
            log_detected_monitors()
        except Exception:
            pass

        # Lightweight Dashboard GUI (CustomTkinter Settings & Monitoring HUD)
        self.dashboard_gui = DashboardGUI(
            app_ref=self,
            on_target_monitor_change=self.on_target_monitor_change,
            on_rms_threshold_change=self.on_rms_threshold_change,
            on_barge_in_threshold_change=self.on_barge_in_threshold_change,
            on_vad_silence_change=self.on_vad_silence_change,
            on_stt_engine_change=self.on_stt_engine_change,
            on_tts_voice_change=self.on_voice_change,
            on_tts_speed_change=self.on_speed_change,
            on_live_toggle=self.on_f20_live_toggle,
            on_emergency_unmute=self.emergency_unmute,
            on_reset_usage=self.on_reset_usage,
            on_exit=self.stop,
            is_live_active_cb=lambda: self.live_copilot.is_running,
            usage_tracker_ref=self.usage_tracker
        )
        # On-Screen Floating Pill HUD Overlay
        self.hud_overlay = HUDOverlay(position="top-center")
        self.live_copilot.on_audio_level = lambda rms: self.hud_overlay.update_audio_level(rms) if hasattr(self, "hud_overlay") and self.hud_overlay else None
        self.live_copilot.on_connected = lambda: self.hud_overlay.show_live() if hasattr(self, "hud_overlay") and self.hud_overlay else None

        if start_gui:
            self.dashboard_gui.start_in_thread()
            self.hud_overlay.start()

        # System Tray Manager (Lightweight init)
        self.tray_manager = TrayManager(
            on_open_dashboard=self.show_dashboard,
            on_reload=self.reload,
            on_emergency_unmute=self.emergency_unmute,
            on_speed_change=self.on_speed_change,
            on_voice_change=self.on_voice_change,
            on_reset_usage=self.on_reset_usage,
            on_read_down=self.on_f16_read_cursor_down,
            on_live_toggle=self.on_f20_live_toggle,
            on_windows_local_tts=self.on_f21_windows_local_tts,
            on_exit=self.stop,
            is_live_active_callback=lambda: self.live_copilot.is_running,
            app_title="Voice Operating Hub",
            current_speed=initial_tts_speed,
            current_voice=initial_tts_voice,
            usage_tracker=self.usage_tracker
        )

        # Hotkey Action Handlers (Single vs Double Click with 300ms Debounce Guard)
        double_click_thresh = getattr(config, "DOUBLE_CLICK_THRESHOLD", 0.30)
        debounce_inter = getattr(config, "DEBOUNCE_INTERVAL", 0.30)
        self.f13_handler = HotkeyActionHandler(
            on_single_click=self.on_f13_single_click,
            on_double_click=self.on_f13_double_click,
            threshold=double_click_thresh,
            debounce_interval=debounce_inter
        )
        self.f14_handler = HotkeyActionHandler(
            on_single_click=self.on_f14_single_click,
            on_double_click=self.on_f14_double_click,
            threshold=double_click_thresh,
            debounce_interval=debounce_inter
        )
        self.f15_handler = HotkeyActionHandler(
            on_single_click=self.on_f15_single_click,
            on_double_click=self.on_f15_double_click,
            threshold=double_click_thresh,
            debounce_interval=debounce_inter
        )
        self.f16_handler = HotkeyActionHandler(
            on_single_click=self.on_f16_single_click,
            on_double_click=self.on_f16_double_click,
            threshold=double_click_thresh,
            debounce_interval=debounce_inter
        )

        # Backward compatibility aliases
        self.f21_handler = self.f13_handler
        self.f22_handler = self.f14_handler
        self.f23_handler = self.f16_handler

        self._hotkey_hooks = []
        self._pynput_listener = None

        # Non-blocking Fast In-Memory Background Engine Warmup Worker
        threading.Thread(target=self._warmup_engines, daemon=True, name="EngineWarmupThread").start()

    @property
    def audio_player(self) -> AudioPlayer:
        """Lazy property for AudioPlayer with finished callback."""
        if self._audio_player is None:
            self._audio_player = AudioPlayer(lazy_init=True)
            self._audio_player.set_on_finished_callback(self._on_tts_playback_finished)
        return self._audio_player

    @audio_player.setter
    def audio_player(self, val: AudioPlayer):
        self._audio_player = val

    @property
    def live_audio_producer(self) -> LiveAudioStreamProducer:
        """Lazy property for LiveAudioStreamProducer."""
        if self._live_audio_producer is None:
            self._live_audio_producer = LiveAudioStreamProducer(sample_rate=config.SAMPLE_RATE, channels=config.CHANNELS)
        return self._live_audio_producer

    @live_audio_producer.setter
    def live_audio_producer(self, val: LiveAudioStreamProducer):
        self._live_audio_producer = val

    def _warmup_engines(self):
        """Pre-warms client objects and devices concurrently in background (< 100ms startup dispatch)."""
        t_start = time.perf_counter()
        logger.info("[Warmup] Fast parallel background engine warmup initiated...")

        # 1. Background STT client warmup thread
        def _warm_stt():
            try:
                t0 = time.perf_counter()
                self.engine.warmup()
                logger.info(f"[Warmup: STT] Persistent STT session ready in {(time.perf_counter() - t0)*1000:.1f}ms")
            except Exception as e:
                logger.debug(f"[Warmup Notice: STT] {e}")
        threading.Thread(target=_warm_stt, daemon=True, name="STTWarmupWorker").start()

        # 2. Background TTS client warmup thread
        def _warm_tts():
            try:
                t0 = time.perf_counter()
                self.tts_engine.warmup()
                logger.info(f"[Warmup: TTS] Persistent TTS session ready in {(time.perf_counter() - t0)*1000:.1f}ms")
            except Exception as e:
                logger.debug(f"[Warmup Notice: TTS] {e}")
        threading.Thread(target=_warm_tts, daemon=True, name="TTSWarmupWorker").start()

        # 3. Background Audio Device & COM warmup thread
        def _warm_audio():
            try:
                t0 = time.perf_counter()
                player = self.audio_player
                if hasattr(player, "_init_mixer"):
                    player._init_mixer()
                audio_control._controller._ensure_com()
                logger.info(f"[Warmup: Audio] Device & COM ready in {(time.perf_counter() - t0)*1000:.1f}ms")
            except Exception as e:
                logger.debug(f"[Warmup Notice: Audio] {e}")
        threading.Thread(target=_warm_audio, daemon=True, name="AudioWarmupWorker").start()

        # Mark warmup complete immediately
        self._warmup_event.set()
        total_ms = (time.perf_counter() - t_start) * 1000
        print(f"[Warmup Complete] Background engines dispatched in {total_ms:.1f}ms (Target < 500ms)", flush=True)

    def _ensure_warmup(self, timeout: float = 0.5):
        """Ensures background engine warmup has finished before triggering speech pipelines."""
        if not self._warmup_event.is_set():
            logger.info("[Warmup Wait] Awaiting background engine warmup completion...")
            self._warmup_event.wait(timeout=timeout)

    def sound_feedback(self, freq: int, ms: int):
        """Play asynchronous sound feedback."""
        def _beep():
            try:
                winsound.Beep(freq, ms)
            except Exception:
                pass
        threading.Thread(target=_beep, daemon=True).start()

    def play_start_chime(self):
        """Play distinct crisp ascending chime on STT recording start."""
        try:
            winsound.Beep(987, 50)
            winsound.Beep(1318, 70)
        except Exception:
            pass

    def play_stop_chime(self):
        """Play distinct crisp descending chime on STT recording stop."""
        try:
            winsound.Beep(1318, 50)
            winsound.Beep(784, 80)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # System Tray & Status Updates
    # -------------------------------------------------------------------------

    def update_tray_state(self):
        """Refreshes tray icon color, on-screen floating HUD pill, and dashboard GUI status."""
        if self.is_streaming:
            tooltip = "[🔴 LIVE STREAMING ACTIVE - INGESTING AUDIO (F13 STT)]"
            self.tray_manager.update_status(DaemonStatus.ACTIVE, tooltip)
            if hasattr(self, "hud_overlay") and self.hud_overlay:
                self.hud_overlay.show_stt()
            if hasattr(self, "dashboard_gui") and self.dashboard_gui:
                self.dashboard_gui.update_status("🔴 STT Active (Ingesting Audio)", is_active=True)
        elif hasattr(self, "live_copilot") and self.live_copilot and self.live_copilot.is_running:
            tooltip = "[🔴 LIVE STREAMING ACTIVE - INGESTING AUDIO (F20 Live Co-pilot)]"
            self.tray_manager.update_status(DaemonStatus.ACTIVE, tooltip)
            if hasattr(self, "hud_overlay") and self.hud_overlay:
                self.hud_overlay.show_live()
            if hasattr(self, "dashboard_gui") and self.dashboard_gui:
                self.dashboard_gui.update_status("🤖 Live Co-pilot Active", is_active=True)
        elif hasattr(self, "audio_player") and self.audio_player and (self.audio_player.is_playing() or self.audio_player.is_paused()):
            tooltip = "[🔊 TTS PLAYBACK ACTIVE] - Voice Operating Hub"
            self.tray_manager.update_status(DaemonStatus.ACTIVE, tooltip)
            if hasattr(self, "hud_overlay") and self.hud_overlay:
                self.hud_overlay.show_tts()
            if hasattr(self, "dashboard_gui") and self.dashboard_gui:
                self.dashboard_gui.update_status("🔊 TTS Playing", is_active=True)
        else:
            tooltip = "Voice Operating Hub: Ready (F13: STT, F20: Live Co-pilot)"
            self.tray_manager.update_status(DaemonStatus.READY, tooltip)
            if hasattr(self, "hud_overlay") and self.hud_overlay:
                self.hud_overlay.hide()
            if hasattr(self, "dashboard_gui") and self.dashboard_gui:
                self.dashboard_gui.update_status("Ready", is_active=False)

    def show_dashboard(self):
        """Displays / brings the Dashboard GUI to front."""
        if hasattr(self, "dashboard_gui") and self.dashboard_gui:
            self.dashboard_gui.show()

    def hide_dashboard(self):
        """Hides the Dashboard GUI into system tray."""
        if hasattr(self, "dashboard_gui") and self.dashboard_gui:
            self.dashboard_gui.hide()

    def _on_tts_playback_finished(self):
        logger.info("[App] TTS playback completed.")
        audio_control.unmute()
        self.update_tray_state()

    # -------------------------------------------------------------------------
    # Pipeline A: Speak-to-Cursor (F21)
    # -------------------------------------------------------------------------

    def on_f21_single_click(self):
        logger.info("[Hotkey] F21 Single Click -> Toggling STT stream.")
        self.toggle_stt()

    def on_f21_double_click(self):
        thresh_ms = getattr(config, 'DOUBLE_CLICK_THRESHOLD', 0.30) * 1000
        logger.info(f"[Hotkey] {config.HOTKEY_STT.upper()} Double Click (<{thresh_ms:.0f}ms) -> Emergency STT abort & unmute.")
        self.emergency_flush_stt()

    def _on_live_token_received(self, text: str, is_final: bool = True):
        """Callback invoked when live streaming STT yields interim (real-time typing) or finalized utterance segments."""
        if not text:
            return
        if is_final:
            clean_text = sanitize_text(text, check_dedup=True)
            if clean_text:
                print(f"[Live STT Final Injected]: '{clean_text}'", flush=True)
                self.stream_injector.inject_final(clean_text, add_space=True)
        else:
            clean_text = sanitize_text(text, check_dedup=False)
            if clean_text:
                print(f"[Live STT Interim Stream]: '{clean_text}'", flush=True)
                self.stream_injector.inject_interim(clean_text)

    def toggle_stt(self):
        with self._toggle_lock:
            if not self.is_streaming:
                # 0. If Gemini Live Co-pilot is running, stop it completely to release microphone hardware
                if hasattr(self, "live_copilot") and self.live_copilot and self.live_copilot.is_running:
                    logger.info("[STT Init] Stopping active Live Co-pilot session to release microphone for STT...")
                    self.live_copilot.stop()
                    time.sleep(0.02)

                # 1. Play start chime BEFORE muting master audio so it is crisp and clearly audible
                self.play_start_chime()

                # 2. Instantly mute Windows Master Audio
                audio_control.mute()

                # 3. Start STT stream immediately without waiting for warmup
                self.is_streaming = True
                self._stt_start_time = time.time()
                reset_dedup_memory()
                self.delta_tracker.reset()
                self.vad_segmenter.reset()
                self.stream_injector.reset()
                with self.lock:
                    self.session_audio_frames.clear()
                logger.info(f"[Status] 🟢 Cloud Real-Time Live Streaming Active... (Press {config.HOTKEY_STT.upper()} to Stop)")

                if hasattr(self, "hud_overlay") and self.hud_overlay:
                    self.hud_overlay.show_stt_connecting()

                with self.audio_queue.mutex:
                    self.audio_queue.queue.clear()

                is_streaming_mode = getattr(config, "STT_MODE", "streaming") == "streaming"
                if is_streaming_mode and hasattr(self.engine, "create_live_session"):
                    try:
                        self.active_live_session = self.engine.create_live_session(on_token_callback=self._on_live_token_received)
                        if self.active_live_session:
                            self.active_live_session.start()
                            logger.info("[STT] Real-time Live WebSocket streaming session started (< 300ms latency).")
                    except Exception as ex:
                        logger.error(f"[STT Live Init Error] {ex}")
                        self.active_live_session = None

                if not self.active_live_session:
                    threading.Thread(target=self.audio_worker, daemon=True).start()

                threading.Thread(target=self.stream_capture, daemon=True).start()
                if hasattr(self, "hud_overlay") and self.hud_overlay:
                    self.hud_overlay.show_stt()
                self.update_tray_state()
            else:
                # 1. Stop mic stream immediately
                self.is_streaming = False
                if hasattr(self, "hud_overlay") and self.hud_overlay:
                    self.hud_overlay.show_stt_finalizing()
                stt_duration = max(0.0, time.time() - getattr(self, "_stt_start_time", time.time()))
                self.usage_tracker.record_stt(stt_duration)
                print(f"[STT Stop] Stopped after {stt_duration:.2f}s -> flushing trailing audio...", flush=True)

                if self.active_live_session:
                    try:
                        self.active_live_session.stop()
                    except Exception as ex:
                        logger.debug(f"[STT Live Stop Error] {ex}")
                    self.active_live_session = None
                    self.stream_injector.reset()
                else:
                    # Final Flush for Batch Mode: Process only any remaining trailing audio slice (< 500ms)
                    with self.lock:
                        all_pcm = b"".join(self.session_audio_frames) if self.session_audio_frames else b""
                        self.session_audio_frames.clear()

                    if all_pcm and len(all_pcm) >= int(config.SAMPLE_RATE * 2 * 0.15): # >= 150ms
                        session_audio_int16 = np.frombuffer(all_pcm, dtype=np.int16)
                        final_audio = session_audio_int16.astype(np.float32) / 32768.0
                        duration = len(final_audio) / config.SAMPLE_RATE
                        print(f"[Final Flush] Transcribing trailing audio snippet ({duration:.2f}s, {len(all_pcm)} bytes)...", flush=True)
                        try:
                            final_text = self.engine.transcribe(final_audio)
                            if final_text:
                                final_delta = self.delta_tracker.process_incoming_text(final_text)
                                if final_delta:
                                    print(f"[Final Flush Live Injected]: '{final_delta}'", flush=True)
                                    inject_delta_text(final_delta)
                        except Exception as ex:
                            logger.error(f"[Final Flush Error] {ex}")

                # 3. Restore/Unmute Windows Master Audio BEFORE playing stop chime so it is heard
                audio_control.unmute()
                self.play_stop_chime()
                logger.info("[STT] ⏹️ Stopped recording & audio unmuted.")
                self.update_tray_state()

    # Alias for backward compatibility
    toggle = toggle_stt

    def emergency_flush_stt(self):
        """Emergency flush all audio buffers, abort cursor typing, and restore master audio."""
        with self.lock:
            self.is_streaming = False
            if self.active_live_session:
                try:
                    self.active_live_session.stop()
                except Exception:
                    pass
                self.active_live_session = None
            self.session_audio_frames.clear()
            self.delta_tracker.reset()
            self.vad_segmenter.reset()
            with self.audio_queue.mutex:
                self.audio_queue.queue.clear()
            audio_control.unmute()
            self.sound_feedback(300, 200)
            logger.info("[STT] ⚠️ Emergency Abort triggered: queues cleared & audio unmuted.")
            self.update_tray_state()

    def audio_worker(self):
        while self.is_streaming:
            try:
                segment = self.audio_queue.get(timeout=0.15)
            except queue.Empty:
                continue

            # Latency Protection: Drop stale backlog only if queue is excessively backed up (> 3 chunks)
            while self.audio_queue.qsize() > 3:
                try:
                    dropped_segment = self.audio_queue.get_nowait()
                    print("[Latency Guard] Dropped stale backlog audio chunk -> Catching up with latest speech", flush=True)
                    segment = dropped_segment
                    self.audio_queue.task_done()
                except queue.Empty:
                    break

            duration = len(segment) / config.SAMPLE_RATE
            print(f"[STT Live Worker] Transcribing {duration:.2f}s live speech chunk...", flush=True)

            try:
                # Transcribe speech chunk
                raw_text = self.engine.transcribe(segment)
                if raw_text:
                    delta = self.delta_tracker.process_incoming_text(raw_text)
                    if delta:
                        print(f"[Live STT Injected]: '{delta}'", flush=True)
                        inject_delta_text(delta)
                else:
                    print(f"[STT Notice] Model returned empty text for {duration:.2f}s slice", flush=True)
            except Exception as e:
                logger.error(f"[STT Error] Transcription failed: {e}")
                self.tray_manager.update_status(DaemonStatus.ERROR, f"STT Error: {e}")
            finally:
                self.audio_queue.task_done()

    def stream_capture(self):
        bytes_counter = 0
        last_log_time = time.time()
        current_speech_frames = []
        current_silence_ms = 0
        voiced_frames_count = 0

        frame_duration_ms = config.FRAME_DURATION_MS # 30ms
        overlap_frames_count = int(getattr(config, "OVERLAP_MS", 240) / frame_duration_ms) # 240ms phonetic boundary overlap
        min_speech_bytes = int(config.SAMPLE_RATE * 2 * (getattr(config, "MIN_SPEECH_DURATION_MS", 150) / 1000.0)) # ~150ms
        max_slice_frames = int(getattr(config, "MAX_CONTINUOUS_SPEECH_MS", 1200) / frame_duration_ms) # 1.2s adaptive slice window (40 frames)
        rms_threshold = getattr(config, "RMS_THRESHOLD", 250.0)
        silence_cutoff_ms = int(getattr(config, "VAD_SILENCE_MS", 280)) # Ultra-low latency silence cutoff (250-300ms)

        try:
            for data_bytes in robust_audio_stream_capture(
                is_active_callback=lambda: self.is_streaming,
                sample_rate=config.SAMPLE_RATE,
                channels=config.CHANNELS,
                frame_samples=int(config.SAMPLE_RATE * (config.FRAME_DURATION_MS / 1000.0))
            ):
                if not self.is_streaming:
                    break

                bytes_counter += len(data_bytes)
                pcm_array = np.frombuffer(data_bytes, dtype=np.int16)
                rms = calculate_rms(pcm_array)
                if hasattr(self, "dashboard_gui") and self.dashboard_gui:
                    self.dashboard_gui.update_audio_level(rms)
                if hasattr(self, "hud_overlay") and self.hud_overlay:
                    self.hud_overlay.update_audio_level(rms)

                # Real-Time WebSocket Streaming Mode (when active_live_session is started)
                if self.active_live_session:
                    self.active_live_session.send_audio_chunk(data_bytes)
                    now = time.time()
                    if now - last_log_time >= 2.0:
                        print(f"[Mic Capture Live] Streaming 16kHz PCM ({bytes_counter}B) | RMS: {rms:.1f}", flush=True)
                        last_log_time = now
                    continue

                # Batch Slicing Mode (Gemini 2.5 Flash Keep-Alive Slices)
                is_speech = self.vad_segmenter.vad.is_speech(data_bytes, config.SAMPLE_RATE)

                # Keep track of trailing audio in session_audio_frames
                with self.lock:
                    self.session_audio_frames.append(data_bytes)
                current_speech_frames.append(data_bytes)

                if is_speech and rms >= rms_threshold:
                    voiced_frames_count += 1
                    current_silence_ms = 0
                elif rms >= (rms_threshold * 0.85):
                    # Slight sound sustain
                    current_silence_ms = 0
                else:
                    current_silence_ms += frame_duration_ms

                # Periodic diagnostic log every ~1.5s
                now = time.time()
                if now - last_log_time >= 1.5:
                    buffered_ms = len(current_speech_frames) * frame_duration_ms
                    print(f"[Mic Capture] Ingested {bytes_counter}B | RMS: {rms:.1f} | Speech: {is_speech} (Voiced frames: {voiced_frames_count}) | Buffered: {buffered_ms}ms", flush=True)
                    last_log_time = now

                # Condition a: Primary Natural Pause Priority (Silence >= silence_cutoff_ms e.g. 280ms after speech)
                # Condition b: Secondary Adaptive Slice Window (1.2s continuous speech)
                should_dispatch = False
                dispatch_reason = ""

                if voiced_frames_count > 0:
                    if current_silence_ms >= silence_cutoff_ms:
                        should_dispatch = True
                        dispatch_reason = f"Natural Pause ({current_silence_ms}ms silence)"
                    elif len(current_speech_frames) >= max_slice_frames:
                        should_dispatch = True
                        dispatch_reason = f"Adaptive Slice ({len(current_speech_frames)*frame_duration_ms/1000.0:.1f}s speech)"
                elif len(current_speech_frames) >= max_slice_frames:
                    # Discard pure silence/noise accumulation without sending to Gemini
                    current_speech_frames.clear()
                    current_silence_ms = 0
                    with self.lock:
                        self.session_audio_frames.clear()

                if should_dispatch:
                    pcm_slice = b"".join(current_speech_frames)
                    
                    # Retain 300ms phonetic overlap from the end of the slice for the next chunk
                    overlap_tail = current_speech_frames[-overlap_frames_count:] if len(current_speech_frames) >= overlap_frames_count else list(current_speech_frames)
                    current_speech_frames.clear()
                    current_speech_frames.extend(overlap_tail)

                    voiced_frames_count = 0
                    current_silence_ms = 0

                    with self.lock:
                        self.session_audio_frames.clear()

                    if len(pcm_slice) >= min_speech_bytes:
                        audio_int16 = np.frombuffer(pcm_slice, dtype=np.int16)
                        slice_rms = calculate_rms(audio_int16)
                        if slice_rms >= rms_threshold:
                            audio_float32 = audio_int16.astype(np.float32) / 32768.0
                            duration = len(audio_float32) / config.SAMPLE_RATE
                            print(f"[Live Slice Cut] {dispatch_reason} (RMS {slice_rms:.1f}) -> Dispatched {duration:.2f}s chunk (w/ 350ms overlap)", flush=True)
                            try:
                                self.audio_queue.put_nowait(audio_float32)
                            except queue.Full:
                                try:
                                    _ = self.audio_queue.get_nowait()
                                except queue.Empty:
                                    pass
                        else:
                            print(f"[Noise Gate] Discarded low-energy slice ({len(audio_int16)/config.SAMPLE_RATE:.2f}s, RMS {slice_rms:.1f} < {rms_threshold})", flush=True)
        except Exception as e:
            if self.is_streaming:
                logger.error(f"[Stream Error] Microphone capture error: {e}")
                self.tray_manager.update_status(DaemonStatus.ERROR, f"Mic Error: {e}")

    # -------------------------------------------------------------------------
    # Pipeline B: Read-to-Ear (F14: Read Selected | F15: Read Cursor Down | F16: Play/Pause)
    # -------------------------------------------------------------------------

    def _stream_text_to_tts(self, text: str):
        """Chunk sentences, prefetch TTS audio in parallel, and play immediately (< 300ms)."""
        if not text or not text.strip():
            logger.warning("[TTS] No text found to read.")
            return

        session_id = time.time()
        self._current_tts_session = session_id

        def _process_stream_read():
            try:
                self._ensure_warmup()
                chunks = split_text_chunks(text, first_chunk_max_len=40, normal_chunk_max_len=140)
                if not chunks:
                    logger.warning("[TTS] No valid text chunks to read.")
                    return

                total_chars = sum(len(c) for c in chunks)
                is_neural = "Neural2" in getattr(self.tts_engine, "voice_name", "Neural2")
                self.usage_tracker.record_tts(total_chars, is_neural=is_neural)
                logger.info(f"[TTS Pipeline] Split text into {len(chunks)} chunks ({total_chars} chars). Starting parallel prefetch streaming...")
                self.update_tray_state()

                if len(chunks) == 1:
                    t0 = time.perf_counter()
                    chunk_audio = self.tts_engine.synthesize(chunks[0])
                    synth_ms = (time.perf_counter() - t0) * 1000
                    if self._current_tts_session == session_id and chunk_audio:
                        logger.info(f"[TTS Pipeline] Single chunk synthesized in {synth_ms:.1f}ms ({len(chunk_audio)}B). Starting playback...")
                        audio_control.unmute()
                        self.audio_player.start_queue_playback(chunk_audio, is_last=True)
                        self.update_tray_state()
                    return

                # Multiple chunks: Parallel pre-fetching of Chunk 0 and Chunk 1
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    t0 = time.perf_counter()
                    fut_0 = executor.submit(self.tts_engine.synthesize, chunks[0])
                    fut_1 = executor.submit(self.tts_engine.synthesize, chunks[1])

                    # Wait for Chunk 0 -> start playback immediately (< 300ms)
                    chunk_0_audio = fut_0.result()
                    synth_0_ms = (time.perf_counter() - t0) * 1000

                    if self._current_tts_session != session_id or not self.is_running:
                        return

                    if chunk_0_audio:
                        logger.info(f"[TTS Pipeline] First chunk synthesized in {synth_0_ms:.1f}ms ({len(chunk_0_audio)}B). Instant TTFA playback!")
                        audio_control.unmute()
                        self.audio_player.start_queue_playback(chunk_0_audio, is_last=False)
                        self.update_tray_state()

                    # Wait for Chunk 1 -> enqueue to continuous stream
                    chunk_1_audio = fut_1.result()
                    if self._current_tts_session != session_id or not self.is_running:
                        return

                    if chunk_1_audio:
                        self.audio_player.enqueue_chunk(chunk_1_audio, is_last=(len(chunks) == 2))

                # Sequentially pre-fetch remaining chunks (2, 3, ...) in background
                for idx in range(2, len(chunks)):
                    if self._current_tts_session != session_id or not self.is_running:
                        break
                    chunk_audio = self.tts_engine.synthesize(chunks[idx])
                    if self._current_tts_session != session_id or not self.is_running:
                        break
                    if chunk_audio:
                        is_last = (idx == len(chunks) - 1)
                        self.audio_player.enqueue_chunk(chunk_audio, is_last=is_last)

            except Exception as e:
                logger.error(f"[TTS Error] Pipeline B stream execution failed: {e}")
                self.tray_manager.update_status(DaemonStatus.ERROR, f"TTS Error: {e}")
                self.update_tray_state()

        threading.Thread(target=_process_stream_read, daemon=True, name="TTSPrefetchWorker").start()

    # F13: STT Handlers
    def on_f13_single_click(self):
        hotkey = getattr(config, 'HOTKEY_STT', 'f13').upper()
        logger.info(f"[Hotkey] {hotkey} Single Click -> Toggle Streaming STT.")
        self.toggle_stt()

    def on_f13_double_click(self):
        thresh_ms = getattr(config, 'DOUBLE_CLICK_THRESHOLD', 0.30) * 1000
        logger.info(f"[Hotkey] {config.HOTKEY_STT.upper()} Double Click (<{thresh_ms:.0f}ms) -> Emergency STT abort & unmute.")
        self.emergency_flush_stt()

    toggle_streaming = toggle_stt
    toggle = toggle_stt
    on_f13_stt_toggle = on_f13_single_click
    on_f21_single_click = on_f13_single_click
    on_f21_double_click = on_f13_double_click

    # F14: Read Selected Only
    def on_f14_single_click(self):
        hotkey = getattr(config, 'HOTKEY_TTS_READ_SEL', 'f14').upper()
        logger.info(f"[Hotkey] {hotkey} Single Click -> Read highlighted/selected text only.")
        self.on_f14_read_selected_only()

    def on_f14_double_click(self):
        thresh_ms = getattr(config, 'DOUBLE_CLICK_THRESHOLD', 0.30) * 1000
        logger.info(f"[Hotkey] Double Click (<{thresh_ms:.0f}ms) -> Force stop TTS playback.")
        self.emergency_stop_tts()

    def on_f14_read_selected_only(self):
        """Capture already highlighted/selected text directly via Ctrl+C and stream to TTS."""
        if self.audio_player.is_playing() or self.audio_player.is_paused():
            logger.info("[TTS] Stopping previous audio playback...")
            self.audio_player.stop()

        logger.info("[TTS] Capturing highlighted/selected text...")
        self.sound_feedback(750, 50)
        text = copy_selected_text(wait_seconds=getattr(config, "COPY_WAIT_SECONDS", 0.05))
        self._stream_text_to_tts(text)

    on_f22_single_click = on_f14_single_click
    on_f22_double_click = on_f14_double_click
    on_f22_read_selected = on_f14_read_selected_only
    handle_tts_action = on_f14_read_selected_only

    # F15: Read Cursor to Bottom
    def on_f15_single_click(self):
        hotkey = getattr(config, 'HOTKEY_TTS_READ_DOWN', 'f15').upper()
        logger.info(f"[Hotkey] {hotkey} Single Click -> Select from cursor to bottom and read.")
        self.on_f15_read_cursor_down()

    def on_f15_double_click(self):
        thresh_ms = getattr(config, 'DOUBLE_CLICK_THRESHOLD', 0.30) * 1000
        logger.info(f"[Hotkey] Double Click (<{thresh_ms:.0f}ms) -> Force stop TTS playback.")
        self.emergency_stop_tts()

    def on_f15_read_cursor_down(self):
        """Select from active cursor to document bottom and stream to TTS (or Toggle Stop if playing)."""
        if self.audio_player.is_playing() or self.audio_player.is_paused():
            logger.info("[TTS] Stopping active playback (Toggle Stop)...")
            self._current_tts_session = 0.0
            self.audio_player.stop()
            audio_control.unmute()
            self.sound_feedback(600, 60)
            self.update_tray_state()
            return

        logger.info("[TTS] Capturing text from active cursor to bottom...")
        self.sound_feedback(750, 50)
        text = copy_cursor_to_bottom(wait_seconds=getattr(config, "COPY_WAIT_SECONDS", 0.08))
        self._stream_text_to_tts(text)

    # F16: Read Cursor to Bottom (identical to F15)
    def on_f16_single_click(self):
        hotkey = getattr(config, 'HOTKEY_TTS_TOGGLE', 'f16').upper()
        logger.info(f"[Hotkey] {hotkey} Single Click -> Select from cursor to bottom and read (TTS Read Down).")
        self.on_f16_read_cursor_down()

    def on_f16_double_click(self):
        thresh_ms = getattr(config, 'DOUBLE_CLICK_THRESHOLD', 0.30) * 1000
        logger.info(f"[Hotkey] Double Click (<{thresh_ms:.0f}ms) -> Force stop TTS playback.")
        self.emergency_stop_tts()

    def on_f16_read_cursor_down(self):
        """Select from active cursor to document bottom and stream to TTS (identical to F15)."""
        self.on_f15_read_cursor_down()

    on_f16_toggle_play_pause = on_f16_read_cursor_down
    on_f16_tts_toggle = on_f16_read_cursor_down
    on_f23_single_click = on_f16_single_click
    on_f23_double_click = on_f16_double_click
    on_f23_toggle_play_pause = on_f16_read_cursor_down
    on_f23_read_down = on_f16_read_cursor_down

    def emergency_stop_tts(self):
        """Emergency stop TTS playback and cancel background prefetch workers immediately."""
        self._current_tts_session = 0.0
        self.audio_player.stop()
        audio_control.unmute()
        self.sound_feedback(300, 150)
        logger.info("[TTS] ⚠️ Emergency TTS playback stop triggered.")
        self.update_tray_state()

    def on_f21_windows_local_tts(self):
        """
        Dedicated F21 Handler: Windows Native Local TTS (Offline 100%).
        - If AudioPlayer is currently playing or paused: Stop immediately (Toggle Stop).
        - If idle: Copies highlighted/selected text, synthesizes via WindowsNativeTTSEngine,
          and plays directly through AudioPlayer without any internet/cloud latency.
        """
        if self.audio_player.is_playing() or self.audio_player.is_paused():
            logger.info("[Local TTS] Stopping active playback (F21 Toggle)...")
            self.audio_player.stop()
            self.sound_feedback(440, 50)
            self.update_tray_state()
            return

        logger.info("[Local TTS] Capturing selected text for Windows Native TTS...")
        self.sound_feedback(800, 40)
        text = copy_selected_text(wait_seconds=getattr(config, "COPY_WAIT_SECONDS", 0.05))
        if not text or not text.strip():
            logger.warning("[Local TTS] No text selected to read.")
            self.sound_feedback(300, 100)
            return

        def _synthesize_and_play():
            try:
                wav_bytes = self.local_tts_engine.synthesize_to_bytes(text)
                if wav_bytes:
                    self.audio_player.play(wav_bytes)
                    self.update_tray_state()
                else:
                    logger.warning("[Local TTS] Synthesis produced empty audio bytes.")
                    self.sound_feedback(300, 100)
            except Exception as e:
                logger.error(f"[Local TTS Error] Failed during synthesis/playback: {e}")
                self.sound_feedback(300, 100)

        threading.Thread(target=_synthesize_and_play, daemon=True, name="WindowsLocalTTSWorker").start()

    def on_speed_change(self, speed: float):
        """Dynamic runtime update: user selected a new speech speed."""
        logger.info(f"[App] Setting speech speed to {speed:.2f}x")
        config.TTS_SPEAKING_RATE = speed
        self.tts_engine.set_speed(speed)
        if hasattr(self, "local_tts_engine") and self.local_tts_engine:
            self.local_tts_engine.set_speed(speed)
        if hasattr(self, "tray_manager") and self.tray_manager:
            self.tray_manager.current_speed = speed
        if hasattr(self, "dashboard_gui") and self.dashboard_gui:
            self.dashboard_gui.current_tts_speed = speed
        self.sound_feedback(int(700 * speed), 50)

    def on_voice_change(self, voice: str):
        """Dynamic runtime update: user selected a new female voice."""
        logger.info(f"[App] Setting active female voice to '{voice}'")
        config.TTS_VOICE = voice
        config.GCP_TTS_VOICE = voice
        self.tts_engine.set_voice(voice)
        if hasattr(self, "tray_manager") and self.tray_manager:
            self.tray_manager.current_voice = voice
        if hasattr(self, "dashboard_gui") and self.dashboard_gui:
            self.dashboard_gui.current_tts_voice = voice
        self.sound_feedback(850, 50)

    def on_target_monitor_change(self, monitor_idx: int):
        """Dynamic runtime update: target monitor for screen capture & Live co-pilot."""
        logger.info(f"[App Dynamic Config] Target monitor set to Monitor {monitor_idx}")
        config.GEMINI_LIVE_TARGET_MONITOR = monitor_idx
        if hasattr(self, "live_copilot") and self.live_copilot:
            self.live_copilot.target_monitor = monitor_idx

    def on_rms_threshold_change(self, threshold: float):
        """Dynamic runtime update: microphone RMS sensitivity threshold."""
        logger.info(f"[App Dynamic Config] Mic RMS threshold set to {threshold:.1f}")
        config.RMS_THRESHOLD = float(threshold)

    def on_barge_in_threshold_change(self, threshold: float):
        """Dynamic runtime update: Gemini Live Co-pilot Barge-in RMS sensitivity threshold."""
        logger.info(f"[App Dynamic Config] Live Barge-in RMS threshold set to {threshold:.1f}")
        config.GEMINI_LIVE_RMS_THRESHOLD = float(threshold)
        if hasattr(self, "live_copilot") and self.live_copilot:
            self.live_copilot.noise_threshold = float(threshold)

    def on_vad_silence_change(self, silence_ms: int):
        """Dynamic runtime update: VAD silence cutoff duration window."""
        logger.info(f"[App Dynamic Config] VAD silence cutoff set to {silence_ms}ms")
        config.VAD_SILENCE_MS = int(silence_ms)
        if hasattr(self, "vad_segmenter") and self.vad_segmenter:
            self.vad_segmenter.silence_limit_frames = max(1, int(silence_ms / 30))

    def on_stt_engine_change(self, engine_type: str):
        """Dynamic runtime update: switch primary STT engine."""
        logger.info(f"[App Dynamic Config] Primary STT engine switched to '{engine_type}'")
        config.STT_ENGINE = engine_type
        if hasattr(self, "engine") and self.engine:
            self.engine.engine_type = engine_type
            if hasattr(self.engine, "_cached_engine"):
                self.engine._cached_engine = None

    def on_reset_usage(self):
        """User requested usage statistics reset."""
        logger.info("[App] Resetting usage statistics...")
        self.usage_tracker.reset_stats()
        self.sound_feedback(1000, 80)
        self.update_tray_state()

    # -------------------------------------------------------------------------
    # Pipeline C: Multi-Monitor Screen Capture to Clipboard (F17, F18, F19)
    # -------------------------------------------------------------------------

    def on_capture_monitor_1(self):
        hotkey = getattr(config, "HOTKEY_CAP_MON1", "f17").upper()
        logger.info(f"[Hotkey] {hotkey} Pressed -> Capture Monitor 1 to Clipboard.")
        success = capture_monitor_to_clipboard(1)
        if success:
            self.sound_feedback(1200, 70)
        else:
            self.sound_feedback(300, 120)

    def on_capture_monitor_2(self):
        hotkey = getattr(config, "HOTKEY_CAP_MON2", "f18").upper()
        logger.info(f"[Hotkey] {hotkey} Pressed -> Capture Monitor 2 to Clipboard.")
        success = capture_monitor_to_clipboard(2)
        if success:
            self.sound_feedback(1200, 70)
        else:
            self.sound_feedback(300, 120)

    def on_capture_monitor_3(self):
        hotkey = getattr(config, "HOTKEY_CAP_MON3", "f19").upper()
        logger.info(f"[Hotkey] {hotkey} Pressed -> Capture Monitor 3 to Clipboard.")
        success = capture_monitor_to_clipboard(3)
        if success:
            self.sound_feedback(1200, 70)
        else:
            self.sound_feedback(300, 120)

    # -------------------------------------------------------------------------
    # Pipeline D: Gemini Multimodal Live Co-pilot (F20)
    # -------------------------------------------------------------------------

    def on_f20_live_toggle(self):
        """Toggle Gemini Multimodal Live Co-pilot streaming session (F20) asynchronously."""
        def _toggle_worker():
            try:
                # If STT recording is currently active, stop it first to free the mic
                if self.is_streaming:
                    logger.info("[LiveCoPilot Init] Stopping active STT stream to allocate microphone to Live Co-pilot...")
                    self.toggle_stt()
                    time.sleep(0.02)

                if not self.live_copilot.is_running:
                    if hasattr(self, "hud_overlay") and self.hud_overlay:
                        self.hud_overlay.show_live_connecting()
                else:
                    if hasattr(self, "hud_overlay") and self.hud_overlay:
                        self.hud_overlay.show_live_closing()

                is_active = self.live_copilot.toggle()

                if not is_active:
                    if hasattr(self, "hud_overlay") and self.hud_overlay:
                        self.hud_overlay.hide()

                status_str = "ACTIVE (🟢 ON)" if is_active else "STOPPED (OFF)"
                logger.info(f"[Live Co-pilot] Session status toggled -> {status_str}")
                self.tray_manager.notify("Gemini Live Co-pilot", f"Live Session: {status_str}")
            except Exception as ex:
                logger.error(f"[Live Co-pilot Error] Failed to toggle live session: {ex}")
            finally:
                self.update_tray_state()

        threading.Thread(target=_toggle_worker, daemon=True, name="LiveToggleWorker").start()

    # -------------------------------------------------------------------------
    # Daemon Lifecycle & Management
    # -------------------------------------------------------------------------

    def emergency_unmute(self):
        """Tray action callback for manual master audio un-muting."""
        logger.info("[App] Manual Emergency Unmute requested from tray menu.")
        audio_control.unmute()
        self.sound_feedback(500, 100)

    def reload(self):
        """Reloads configuration, resets pipelines, and clears audio state."""
        logger.info("[App] Reloading Voice Operating Hub configurations...")
        with self.lock:
            if self.is_streaming:
                self.is_streaming = False
            self.audio_player.stop()
            self.live_copilot.stop()
            audio_control.unmute()
            with self.audio_queue.mutex:
                self.audio_queue.queue.clear()
            self.session_audio_frames.clear()
            self.delta_tracker.reset()
            self.vad_segmenter.reset()

        self.sound_feedback(1000, 150)
        logger.info("[App] Reload completed successfully.")
        self.update_tray_state()

    def _register_hotkeys(self):
        """Registers low-level Windows keyboard hooks and fallback listener for F13–F20."""
        primary_success = False
        hotkey_stt = getattr(config, "HOTKEY_STT", "f13").lower()
        hotkey_tts_sel = getattr(config, "HOTKEY_TTS_READ_SEL", "f14").lower()
        hotkey_tts_down = getattr(config, "HOTKEY_TTS_READ_DOWN", "f15").lower()
        hotkey_toggle = getattr(config, "HOTKEY_TTS_TOGGLE", "f16").lower()
        hotkey_mon1 = getattr(config, "HOTKEY_CAP_MON1", "f17").lower()
        hotkey_mon2 = getattr(config, "HOTKEY_CAP_MON2", "f18").lower()
        hotkey_mon3 = getattr(config, "HOTKEY_CAP_MON3", "f19").lower()
        hotkey_live = getattr(config, "HOTKEY_LIVE_COPILOT", "f20").lower()
        hotkey_win_tts = getattr(config, "HOTKEY_WINDOWS_LOCAL_TTS", "f21").lower()

        try:
            keyboard.hook_key(hotkey_stt, lambda e: self.f13_handler.handle_press() if e.event_type == keyboard.KEY_DOWN else self.f13_handler.handle_release(), suppress=False)
            keyboard.hook_key(hotkey_tts_sel, lambda e: self.f14_handler.handle_press() if e.event_type == keyboard.KEY_DOWN else self.f14_handler.handle_release(), suppress=False)
            keyboard.hook_key(hotkey_tts_down, lambda e: self.f15_handler.handle_press() if e.event_type == keyboard.KEY_DOWN else self.f15_handler.handle_release(), suppress=False)
            keyboard.hook_key(hotkey_toggle, lambda e: self.f16_handler.handle_press() if e.event_type == keyboard.KEY_DOWN else self.f16_handler.handle_release(), suppress=False)
            keyboard.hook_key(hotkey_mon1, lambda e: self.on_capture_monitor_1() if e.event_type == keyboard.KEY_DOWN else None, suppress=False)
            keyboard.hook_key(hotkey_mon2, lambda e: self.on_capture_monitor_2() if e.event_type == keyboard.KEY_DOWN else None, suppress=False)
            keyboard.hook_key(hotkey_mon3, lambda e: self.on_capture_monitor_3() if e.event_type == keyboard.KEY_DOWN else None, suppress=False)
            keyboard.hook_key(hotkey_live, lambda e: self.on_f20_live_toggle() if e.event_type == keyboard.KEY_DOWN else None, suppress=False)
            keyboard.hook_key(hotkey_win_tts, lambda e: self.on_f21_windows_local_tts() if e.event_type == keyboard.KEY_DOWN else None, suppress=False)

            self._hotkey_hooks.extend([hotkey_stt, hotkey_tts_sel, hotkey_tts_down, hotkey_toggle, hotkey_mon1, hotkey_mon2, hotkey_mon3, hotkey_live, hotkey_win_tts])
            primary_success = True
            logger.info(f"[Hotkey Hooks] Direct low-level key hooks active for {hotkey_stt.upper()}, {hotkey_tts_sel.upper()}, {hotkey_tts_down.upper()}, {hotkey_toggle.upper()}, {hotkey_mon1.upper()}, {hotkey_mon2.upper()}, {hotkey_mon3.upper()}, {hotkey_live.upper()}, {hotkey_win_tts.upper()}")
        except Exception as e:
            logger.warning(f"[Hotkey Hooks Warning] Primary hook setup failed: {e}")

        if not primary_success:
            try:
                def _on_pynput_press(key):
                    key_name = (getattr(key, 'name', None) or str(key).strip("'")).lower()
                    if key_name == hotkey_stt:
                        self.f13_handler.handle_press()
                    elif key_name == hotkey_tts_sel:
                        self.f14_handler.handle_press()
                    elif key_name == hotkey_tts_down:
                        self.f15_handler.handle_release()
                    elif key_name == hotkey_toggle:
                        self.f16_handler.handle_press()
                    elif key_name == hotkey_mon1:
                        self.on_capture_monitor_1()
                    elif key_name == hotkey_mon2:
                        self.on_capture_monitor_2()
                    elif key_name == hotkey_mon3:
                        self.on_capture_monitor_3()
                    elif key_name == hotkey_live:
                        self.on_f20_live_toggle()
                    elif key_name == hotkey_win_tts:
                        self.on_f21_windows_local_tts()

                def _on_pynput_release(key):
                    key_name = (getattr(key, 'name', None) or str(key).strip("'")).lower()
                    if key_name == hotkey_stt:
                        self.f13_handler.handle_release()
                    elif key_name == hotkey_tts_sel:
                        self.f14_handler.handle_release()
                    elif key_name == hotkey_tts_down:
                        self.f15_handler.handle_release()
                    elif key_name == hotkey_toggle:
                        self.f16_handler.handle_release()

                self._pynput_listener = pynput_keyboard.Listener(
                    on_press=_on_pynput_press,
                    on_release=_on_pynput_release,
                )
                self._pynput_listener.start()
                logger.info(f"[Fallback Listener] pynput active for {hotkey_stt.upper()}, {hotkey_tts_sel.upper()}, {hotkey_tts_down.upper()}, {hotkey_toggle.upper()}, {hotkey_mon1.upper()}, {hotkey_mon2.upper()}, {hotkey_mon3.upper()}, {hotkey_live.upper()}, {hotkey_win_tts.upper()}")
            except Exception as e:
                logger.warning(f"[Fallback Listener Warning] pynput setup failed: {e}")

    def _unregister_hotkeys(self):
        """Unregisters all active hotkey hooks."""
        for key_name in self._hotkey_hooks:
            try:
                keyboard.unhook_key(key_name)
            except Exception:
                pass
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        self._hotkey_hooks.clear()

        if self._pynput_listener:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None

    def run(self):
        """Starts the daemon, tray icon, and listens for global hotkeys with micro-benchmarks."""
        t_run_start = time.perf_counter()
        hotkey_stt = getattr(config, "HOTKEY_STT", "f13").upper()
        hotkey_tts_sel = getattr(config, "HOTKEY_TTS_READ_SEL", "f14").upper()
        hotkey_tts_down = getattr(config, "HOTKEY_TTS_READ_DOWN", "f15").upper()
        hotkey_toggle = getattr(config, "HOTKEY_TTS_TOGGLE", "f16").upper()
        hotkey_mon1 = getattr(config, "HOTKEY_CAP_MON1", "f17").upper()
        hotkey_mon2 = getattr(config, "HOTKEY_CAP_MON2", "f18").upper()
        hotkey_mon3 = getattr(config, "HOTKEY_CAP_MON3", "f19").upper()
        hotkey_live = getattr(config, "HOTKEY_LIVE_COPILOT", "f20").upper()
        hotkey_win_tts = getattr(config, "HOTKEY_WINDOWS_LOCAL_TTS", "f21").upper()

        print("==================================================")
        print(" Two-Way Voice Operating Hub Daemon")
        print(" • STT Engine:             Google Cloud Speech-to-Text (Streaming gRPC)")
        print(" • TTS Engine:             Google Cloud Text-to-Speech (Neural2-C / Standard-A)")
        print(" • Live Engine:            Gemini Multimodal Live API (Vision + Voice)")
        print(" • Local TTS Engine:       Windows Native SAPI5 / OneCore (Offline 100%)")
        print(f" • STT Speak-to-Cursor Key: [{hotkey_stt}]")
        print(f" • TTS Read Selected Key:  [{hotkey_tts_sel}]")
        print(f" • TTS Read Down Keys:     [{hotkey_tts_down}, {hotkey_toggle}] (Select Down & Read)")
        print(f" • Screen Capture Keys:    [{hotkey_mon1}, {hotkey_mon2}, {hotkey_mon3}] (Mon 1, 2, 3)")
        print(f" • Live Co-pilot Toggle:   [{hotkey_live}] (Barge-in / Multimodal)")
        print(f" • Local TTS Offline Key:   [{hotkey_win_tts}] (Windows Native SAPI5 / OneCore)")
        print(f" • Single-Click Timing:     {getattr(config, 'DOUBLE_CLICK_THRESHOLD', 0.30)*1000:.0f}ms window")
        print(f" • Double-Click Cancel:     < {getattr(config, 'DOUBLE_CLICK_THRESHOLD', 0.30)*1000:.0f}ms")
        print(" • Master Audio Ducking:    pycaw Enabled")
        print("==================================================")

        # 1. Start Tray Icon (Immediate)
        t_tray = time.perf_counter()
        self.tray_manager.start()
        tray_ms = (time.perf_counter() - t_tray) * 1000
        print(f"[Startup Benchmark] Tray Icon started in {tray_ms:.1f}ms", flush=True)

        # 2. Register Hotkeys (Immediate)
        t_hotkey = time.perf_counter()
        self._register_hotkeys()
        hotkey_ms = (time.perf_counter() - t_hotkey) * 1000
        print(f"[Startup Benchmark] Hotkeys [{hotkey_stt}, {hotkey_tts_sel}, {hotkey_tts_down}, {hotkey_toggle}, {hotkey_mon1}, {hotkey_mon2}, {hotkey_mon3}, {hotkey_live}] bound in {hotkey_ms:.1f}ms", flush=True)

        total_startup_ms = (time.perf_counter() - t_run_start) * 1000
        print(f"[Startup Benchmark] Main Thread Startup Complete in {total_startup_ms:.1f}ms (< 100ms)", flush=True)
        logger.info("[Ready] Voice Operating Hub Daemon active.")

        # Keep main thread alive safely using threading.Event
        try:
            while self.is_running and not self._stop_event.is_set():
                try:
                    self._stop_event.wait(timeout=0.5)
                except KeyboardInterrupt:
                    # Ignore spurious Ctrl+C signals generated during simulated clipboard copy/paste
                    if not self.is_running or self._stop_event.is_set():
                        break
        except Exception as e:
            logger.error(f"[App Error] Main loop exception: {e}")
        finally:
            if self.is_running:
                self.stop()

    def stop(self):
        """Cleanly terminates all background threads and releases OS resources."""
        if not self.is_running and self._stop_event.is_set():
            return

        logger.info("[App] Stopping Voice Operating Hub Daemon...")
        self.is_running = False
        self._stop_event.set()
        with self.lock:
            if self.is_streaming:
                self.is_streaming = False
            self.audio_player.stop()
            self.live_copilot.stop()
            audio_control.unmute()

        self._unregister_hotkeys()
        if hasattr(self, "hud_overlay") and self.hud_overlay:
            self.hud_overlay.stop()
        if hasattr(self, "dashboard_gui") and self.dashboard_gui:
            self.dashboard_gui.destroy()
        self.tray_manager.stop()
        logger.info("[App] Daemon terminated cleanly.")


# Backward compatibility alias
VoiceInjectorApp = VoiceOperatingHubApp
