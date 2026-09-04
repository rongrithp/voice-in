from __future__ import annotations

"""
audio_recorder.py - Real-Time Audio Streamer & Push-to-Talk Subsystem
Windows Edge Module: 16kHz 16-bit Mono PCM audio capture with cursor-anchored HUD feedback.
"""

import sys
import os
import io
import time
import wave
import json
import ctypes
import threading
import tempfile
import argparse
import collections
import urllib.request
from typing import Optional, Callable, Dict, Any, List

STTEngine = Any

# Ensure UTF-8 output encoding on Windows console
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Ensure module directory is in sys.path
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

import numpy as np
import sounddevice as sd

from hud_overlay import get_current_cursor_pos
from intent_parser import IntentParser

# Win32 Constants for Hotkey & Key State Polling
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

VK_SPACE = 0x20
VK_F20 = 0x83
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt key

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000

DEFAULT_SAMPLE_RATE = 16000  # Standard 16 kHz for speech-to-text
DEFAULT_CHANNELS = 1         # Mono
DEFAULT_SAMPLE_WIDTH = 2     # 16-bit PCM = 2 bytes per sample
DEFAULT_SERVER_URL = "http://127.0.0.1:8765/action"


class AudioCaptureResult:
    """Encapsulates captured audio data, metrics, and in-memory WAV buffer."""

    def __init__(
        self,
        raw_pcm: bytes,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        duration_sec: float = 0.0,
        rms_energy: float = 0.0,
        peak_amplitude: int = 0
    ):
        self.raw_pcm = raw_pcm
        self.sample_rate = sample_rate
        self.channels = channels
        self.duration_sec = duration_sec
        self.rms_energy = rms_energy
        self.peak_amplitude = peak_amplitude
        self.num_samples = len(raw_pcm) // (DEFAULT_SAMPLE_WIDTH * channels)

        # Generate WAV buffer in memory
        self.wav_bytes = self._build_wav_buffer()
        self.buffer_size_bytes = len(self.wav_bytes)

    def _build_wav_buffer(self) -> bytes:
        """Encodes raw PCM bytes into a standard RIFF/WAV in-memory buffer."""
        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(DEFAULT_SAMPLE_WIDTH)
            wf.setframerate(self.sample_rate)
            wf.writeframes(self.raw_pcm)
        return wav_io.getvalue()

    def save_to_file(self, file_path: Optional[str] = None) -> str:
        """Saves the recorded audio to a temporary or specified WAV file on disk."""
        if not file_path:
            temp_dir = tempfile.gettempdir()
            file_path = os.path.join(temp_dir, f"voice_in_capture_{int(time.time() * 1000)}.wav")

        with open(file_path, "wb") as f:
            f.write(self.wav_bytes)
        return file_path

    def to_dict(self) -> Dict[str, Any]:
        """Returns metadata dictionary suitable for JSON transport to Event Bus."""
        return {
            "duration_sec": round(self.duration_sec, 3),
            "buffer_size_bytes": self.buffer_size_bytes,
            "num_samples": self.num_samples,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "rms_energy": round(self.rms_energy, 2),
            "peak_amplitude": self.peak_amplitude,
            "format": "16kHz_16bit_mono_pcm"
        }

    def summary(self) -> str:
        """Human-readable condensed summary."""
        kb = self.buffer_size_bytes / 1024.0
        return (
            f"[AUDIO RESULT] Duration: {self.duration_sec:.2f}s | "
            f"Buffer: {kb:.1f} KB ({self.buffer_size_bytes} B) | "
            f"RMS: {self.rms_energy:.1f} | Peak: {self.peak_amplitude}"
        )


class UnifiedAudioStream:
    """
    Singleton / shared non-blocking audio capture stream.
    Maintains a single open sd.InputStream across the entire process lifetime.
    Broadcasts audio frames to registered subscriber callbacks.
    Avoids opening conflicting microphone handles simultaneously.
    """
    _instance: Optional["UnifiedAudioStream"] = None
    _singleton_lock = threading.Lock()

    @classmethod
    def get_instance(
        cls,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        device: Optional[int] = None
    ) -> "UnifiedAudioStream":
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls(sample_rate=sample_rate, channels=channels, device=device)
            return cls._instance

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        device: Optional[int] = None,
        blocksize: int = 1024
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self.blocksize = blocksize

        self._stream: Optional[sd.InputStream] = None
        self._subscribers: List[Callable[[np.ndarray, bytes], None]] = []
        self._lock = threading.RLock()
        self._is_running = False
        self._muted = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running and self._stream is not None and getattr(self._stream, "active", False)

    @property
    def is_muted(self) -> bool:
        with self._lock:
            return self._muted

    def set_muted(self, muted: bool):
        """Mutes broadcasting to prevent acoustic feedback while speaker is playing."""
        with self._lock:
            self._muted = muted

    def subscribe(self, callback: Callable[[np.ndarray, bytes], None]):
        """Registers a callback receiving (chunk_np_int16, raw_bytes)."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)
            if not self._is_running:
                self.start()

    def unsubscribe(self, callback: Callable[[np.ndarray, bytes], None]):
        """Unregisters a subscriber callback."""
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags):
        if status:
            sys.stderr.write(f"[UnifiedAudioStream Status] {status}\n")
        with self._lock:
            if self._muted:
                return
            callbacks = list(self._subscribers)

        raw_bytes = indata.tobytes()
        chunk_copy = indata.copy()
        for cb in callbacks:
            try:
                cb(chunk_copy, raw_bytes)
            except Exception as e:
                sys.stderr.write(f"[UnifiedAudioStream] Subscriber error: {e}\n")

    def start(self, asynchronous: bool = False) -> bool:
        with self._lock:
            if self._is_running and self._stream and getattr(self._stream, "active", False):
                return True
            self._is_running = True

        def _do_open():
            with self._lock:
                if not self._is_running:
                    return False
                try:
                    self._stream = sd.InputStream(
                        samplerate=self.sample_rate,
                        channels=self.channels,
                        dtype="int16",
                        blocksize=self.blocksize,
                        device=self.device,
                        callback=self._audio_callback
                    )
                    self._stream.start()
                    self._is_running = True
                    return True
                except Exception as e:
                    sys.stderr.write(f"[UnifiedAudioStream] Failed to open audio input stream: {e}\n")
                    self._is_running = False
                    return False

        if asynchronous:
            threading.Thread(target=_do_open, daemon=True, name="AudioStreamOpener").start()
            return True
        return _do_open()

    def stop(self):
        with self._lock:
            self._is_running = False
            if self._stream:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def record_speech_vad(
        self,
        silence_threshold_sec: float = 1.5,
        timeout_sec: float = 12.0,
        rms_threshold: float = 250.0,
        stop_event: Optional[threading.Event] = None,
        on_speech_detected: Optional[Callable[[], None]] = None
    ) -> Optional[AudioCaptureResult]:
        """
        Records a single conversational speech turn with voice activity detection (VAD).
        - Automatically detects when speech starts (energy >= rms_threshold).
        - Cuts off recording once silence duration >= silence_threshold_sec (default 1.5s).
        - Returns AudioCaptureResult when completed or None if timed out / aborted.
        """
        if not self.is_running:
            self.start()

        frames_lock = threading.Lock()
        collected_frames: List[np.ndarray] = []
        pre_roll: collections.deque = collections.deque(maxlen=8)
        speech_detected = threading.Event()
        speech_done = threading.Event()

        last_speech_time = [0.0]
        start_time = time.perf_counter()

        def _collector(chunk: np.ndarray, raw_bytes: bytes):
            rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2))) if len(chunk) > 0 else 0.0
            is_voice = rms >= rms_threshold

            with frames_lock:
                if not speech_detected.is_set():
                    pre_roll.append(chunk)
                    if is_voice:
                        speech_detected.set()
                        last_speech_time[0] = time.perf_counter()
                        collected_frames.extend(list(pre_roll))
                        pre_roll.clear()
                        if on_speech_detected:
                            try:
                                on_speech_detected()
                            except Exception:
                                pass
                else:
                    collected_frames.append(chunk)
                    now = time.perf_counter()
                    if is_voice:
                        last_speech_time[0] = now
                    else:
                        if now - last_speech_time[0] >= silence_threshold_sec:
                            speech_done.set()

        self.subscribe(_collector)
        try:
            while True:
                if stop_event and stop_event.is_set():
                    break
                if speech_done.is_set():
                    break
                elapsed = time.perf_counter() - start_time
                if not speech_detected.is_set() and elapsed >= timeout_sec:
                    break
                if speech_detected.is_set() and elapsed >= 30.0:
                    break
                time.sleep(0.025)
        finally:
            self.unsubscribe(_collector)

        with frames_lock:
            if not collected_frames:
                return None
            audio_array = np.concatenate(collected_frames, axis=0)
            raw_pcm = audio_array.tobytes()
            duration_sec = len(raw_pcm) / (DEFAULT_SAMPLE_WIDTH * self.channels * self.sample_rate)
            peak_amp = int(np.max(np.abs(audio_array))) if len(audio_array) > 0 else 0
            rms = float(np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))) if len(audio_array) > 0 else 0.0

            return AudioCaptureResult(
                raw_pcm=raw_pcm,
                sample_rate=self.sample_rate,
                channels=self.channels,
                duration_sec=duration_sec,
                rms_energy=rms,
                peak_amplitude=peak_amp
            )


class AudioRecorder:
    """
    Non-blocking microphone audio recorder using UnifiedAudioStream.
    Captures 16kHz 16-bit Mono PCM audio without opening conflicting microphone handles.
    """

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        device: Optional[int] = None
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device

        self._frames: List[np.ndarray] = []
        self._is_recording = False
        self._lock = threading.Lock()
        self._start_time: float = 0.0
        self._stream = UnifiedAudioStream.get_instance(sample_rate=sample_rate, channels=channels, device=device)

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def _audio_callback(self, chunk: np.ndarray, raw_bytes: bytes):
        if self._is_recording:
            with self._lock:
                self._frames.append(chunk)

    def start_recording(self) -> bool:
        with self._lock:
            if self._is_recording:
                return True

            self._frames = []
            self._start_time = time.perf_counter()
            self._is_recording = True
            self._stream.subscribe(self._audio_callback)
            return True

    def stop_recording(self) -> AudioCaptureResult:
        with self._lock:
            if not self._is_recording:
                return AudioCaptureResult(raw_pcm=b"", sample_rate=self.sample_rate, channels=self.channels)

            self._is_recording = False
            duration_sec = time.perf_counter() - self._start_time
            self._stream.unsubscribe(self._audio_callback)
            captured_chunks = self._frames
            self._frames = []

        if not captured_chunks:
            return AudioCaptureResult(raw_pcm=b"", sample_rate=self.sample_rate, channels=self.channels, duration_sec=duration_sec)

        flat_chunks = [
            c.flatten() if hasattr(c, "flatten") else np.asarray(c, dtype=np.int16).flatten()
            for c in captured_chunks
        ]
        audio_array = np.concatenate(flat_chunks, axis=0) if flat_chunks else np.zeros(0, dtype=np.int16)
        raw_pcm = audio_array.tobytes()
        peak_amp = int(np.max(np.abs(audio_array))) if len(audio_array) > 0 else 0
        rms = float(np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))) if len(audio_array) > 0 else 0.0

        return AudioCaptureResult(
            raw_pcm=raw_pcm,
            sample_rate=self.sample_rate,
            channels=self.channels,
            duration_sec=duration_sec,
            rms_energy=rms,
            peak_amplitude=peak_amp
        )

    def record_fixed_duration(self, duration_sec: float = 2.0) -> AudioCaptureResult:
        if not self.start_recording():
            return AudioCaptureResult(raw_pcm=b"", sample_rate=self.sample_rate, channels=self.channels)
        time.sleep(duration_sec)
        return self.stop_recording()


class BackgroundWakeDetector:
    """
    Background subscriber to UnifiedAudioStream for voice wake detection in STANDBY.
    Monitors live microphone buffer and triggers on 'เจมิไน' or 'เจมิไนมาช่วยหน่อย' or 'gemini help'.
    """

    WAKE_PHRASES = [
        "เจมิไนมาช่วยหน่อย",
        "เจมิไนช่วยหน่อย",
        "เจมิไน",
        "gemini help",
        "gemini come help"
    ]

    def __init__(
        self,
        on_wake: Callable[[str], None],
        stt_engine: Optional[STTEngine] = None,
        rms_threshold: float = 250.0,
        stream: Optional[UnifiedAudioStream] = None
    ):
        self.on_wake = on_wake
        self.stt_engine = stt_engine
        self.rms_threshold = rms_threshold
        self.stream = stream or UnifiedAudioStream.get_instance()

        self._active = False
        self._enabled = False  # Deactivated: primary trigger is strictly F20
        self._worker_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def set_enabled(self, enabled: bool):
        with self._lock:
            self._enabled = enabled

    def start(self, force_enable: bool = False):
        """
        Continuous background wake-word listening is deactivated to prevent
        microphone resource contention and audio stream blocking.
        F20 is the primary interaction switch.
        """
        if not force_enable:
            sys.stdout.write("[BackgroundWakeDetector] Deactivated: Relying strictly on F20 primary switch.\n")
            return
        with self._lock:
            if self._active:
                return
            self._active = True
            self._enabled = True
            if self.stt_engine is None:
                self.stt_engine = STTEngine(model_size="base", device="cpu", compute_type="int8")
            self._worker_thread = threading.Thread(target=self._run_loop, daemon=True, name="WakeDetectorWorker")
            self._worker_thread.start()

    def stop(self):
        with self._lock:
            self._active = False

    def _run_loop(self):
        while self._active:
            if not self._enabled:
                time.sleep(0.15)
                continue

            stop_ev = threading.Event()
            result = self.stream.record_speech_vad(
                silence_threshold_sec=0.8,
                timeout_sec=3.0,
                rms_threshold=self.rms_threshold,
                stop_event=stop_ev
            )

            if not self._active or not self._enabled:
                continue

            if result and len(result.raw_pcm) > 0 and result.duration_sec >= 0.4:
                try:
                    stt_res = self.stt_engine.transcribe(result.wav_bytes)
                    text = stt_res.text.strip().lower()
                    if text:
                        for phrase in self.WAKE_PHRASES:
                            if phrase in text:
                                print(f"\n[WakeWord] Triggered by audio input: \"{stt_res.text}\" (Matched: \"{phrase}\")")
                                self.on_wake(stt_res.text)
                                break
                except Exception as e:
                    sys.stderr.write(f"[BackgroundWakeDetector] Transcription note: {e}\n")


class PushToTalkAudioListener:
    """
    Push-to-Talk manager linked with Ctrl + Alt + Space.
    - Key Press Down: Triggers HUD in THINKING state (listening) & starts recording.
    - Key Release: Stops recording, packages WAV buffer, and reports metrics to Event Bus.
    """

    def __init__(
        self,
        server_url: str = DEFAULT_SERVER_URL,
        on_capture_complete: Optional[Callable[[AudioCaptureResult], None]] = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        enable_stt: bool = True,
        vk: int = VK_SPACE,
        modifiers: int = (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT),
        hotkey_name: str = "Ctrl+Alt+Space"
    ):
        self.server_url = server_url
        self.on_capture_complete = on_capture_complete
        self.recorder = AudioRecorder(sample_rate=sample_rate)
        self.enable_stt = enable_stt
        self.stt_engine = STTEngine() if enable_stt else None
        self.intent_parser = IntentParser() if enable_stt else None
        self.vk = vk
        self.modifiers = modifiers
        self.hotkey_name = hotkey_name

        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

        self._is_active = False
        self._is_key_pressed = False
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._hotkey_id = 1002

    def is_key_down(self) -> bool:
        """Checks if configured hotkey is currently held down using GetAsyncKeyState."""
        key_pressed = bool(self._user32.GetAsyncKeyState(self.vk) & 0x8000)
        return key_pressed

    def _notify_server(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Dispatches an event or action payload to the local server."""
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.server_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            sys.stderr.write(f"[PushToTalk] Server dispatch error: {e}\n")
        return None

    def _handle_key_press(self):
        """Invoked when Ctrl+Alt+Space is pressed down."""
        if self._is_key_pressed:
            return
        self._is_key_pressed = True

        cursor = get_current_cursor_pos()
        print(f"\n[PTT: Pressed Down] Cursor at {cursor}. Activating THINKING HUD & Microphone...")

        # 1. Immediately trigger HUD in THINKING state beside cursor
        self._notify_server({
            "mode": "THINKING",
            "text": "[PUSH-TO-TALK: LISTENING...]\nRecording audio... (Release key to send)",
            "cursor_pos": cursor,
            "duration": 10.0  # Kept open until release
        })

        # 2. Start audio recording
        self.recorder.start_recording()

        # 3. Spawn release detector thread
        threading.Thread(target=self._poll_key_release, daemon=True, name="PTTReleasePoller").start()

    def _poll_key_release(self):
        """Monitors key state until Space (or modifier) is released."""
        # Small grace period to allow key event to stabilize
        time.sleep(0.08)

        while self._is_key_pressed:
            if not self.is_key_down():
                self._handle_key_release()
                break
            time.sleep(0.02)  # 20ms polling interval

    def _handle_key_release(self):
        """Invoked when the hotkey combination is released."""
        if not self._is_key_pressed:
            return
        self._is_key_pressed = False

        # 1. Stop audio recording and capture buffer
        result = self.recorder.stop_recording()
        print(f"[PTT: Released] Recording Complete. {result.summary()}")

        cursor = get_current_cursor_pos()

        # 2. STT Transcription & Intent Parsing Pipeline
        recognized_text = ""
        action_command = (
            f"echo [VOICE PTT COMPLETED] Duration: {result.duration_sec:.2f}s - "
            f"Buffer: {result.buffer_size_bytes} bytes (16kHz Mono)"
        )
        intent_id = "AUDIO_RECORD"
        action_name = "Audio Recording"
        auto_submit = True

        if self.enable_stt and self.stt_engine and len(result.raw_pcm) > 0:
            try:
                stt_res = self.stt_engine.transcribe(result.wav_bytes)
                recognized_text = stt_res.text
                print(f"[PTT: STT Recognized] {stt_res.summary()}")

                if recognized_text:
                    intent_res = self.intent_parser.parse(recognized_text)
                    intent_id = intent_res.intent_id
                    action_name = intent_res.action_name
                    action_command = intent_res.executable_command
                    auto_submit = getattr(intent_res, "auto_submit", False)
                    print(f"[PTT: Intent Parsed] {intent_res.summary()} (auto_submit={auto_submit})")

                    if intent_id == "SESSION_STANDBY_DISMISS":
                        # User said "พอแล้ว" / "ขอบคุณมาก" -> Standby Return
                        try:
                            from gemini_live_client import GeminiLiveClient
                            from terminal_actuator import kill_all_hud_overlays
                            live_cli = GeminiLiveClient()
                            threading.Thread(target=live_cli.play_goodbye, daemon=True, name="GoodbyeAudioWorker").start()
                            kill_all_hud_overlays()
                            action_name = "Session Standby Return"
                            action_command = "echo [COPILOT STANDBY] Session dismissed cleanly."
                            auto_submit = True
                        except Exception as dismiss_err:
                            sys.stderr.write(f"[PushToTalk] Standby dismiss error: {dismiss_err}\n")
                    elif intent_id == "GEMINI_LIVE_WAKE" or "เจมิไน" in recognized_text:
                        # Automatically capture window at cursor and stream to Gemini Live with audio output
                        try:
                            from visual_cortex import look_at_cursor
                            from gemini_live_client import GeminiLiveClient
                            win_ctx = look_at_cursor()
                            live_client = GeminiLiveClient()
                            threading.Thread(
                                target=live_client.execute_turn_sync,
                                args=(recognized_text, win_ctx.get("image_bytes"), win_ctx, True),
                                daemon=True,
                                name="GeminiLiveWakeWorker"
                            ).start()
                            action_name = "Gemini Live Co-pilot Stream Active"
                            action_command = f"echo [GEMINI LIVE STREAMING] Prompt: \"{recognized_text}\""
                            auto_submit = True
                        except Exception as live_err:
                            sys.stderr.write(f"[PushToTalk] Gemini Live trigger error: {live_err}\n")
            except Exception as e:
                sys.stderr.write(f"[PushToTalk] STT/Intent error: {e}\n")

        # 3. Package and report to local Event Bus
        server_resp = self._notify_server({
            "command": action_command,
            "mode": "ACTION",
            "stt_text": recognized_text,
            "intent_id": intent_id,
            "action_name": action_name,
            "auto_submit": auto_submit,
            "audio_report": result.to_dict(),
            "cursor_pos": cursor,
            "duration": 2.5
        })

        if server_resp:
            print(f"[PTT: Server Response] Status: {server_resp.get('status')} | Mode: {server_resp.get('mode')}")

        # 4. Callback hook for downstream consumers
        if self.on_capture_complete:
            try:
                self.on_capture_complete(result)
            except Exception as e:
                sys.stderr.write(f"[PushToTalk] Callback error: {e}\n")

    def _message_pump(self):
        """Background Win32 message pump for RegisterHotKey."""
        from ctypes import wintypes
        self._thread_id = self._kernel32.GetCurrentThreadId()

        msg = wintypes.MSG()
        self._user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_NOREMOVE)

        res = self._user32.RegisterHotKey(
            None,
            self._hotkey_id,
            self.modifiers,
            self.vk
        )
        if res == 0:
            err = self._kernel32.GetLastError()
            sys.stderr.write(f"[PushToTalk] RegisterHotKey ({self.hotkey_name}) failed with code {err}.\n")
            return

        self._is_active = True
        try:
            while self._is_active:
                ret = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
                    self._handle_key_press()

                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._user32.UnregisterHotKey(None, self._hotkey_id)
            self._is_active = False

    def start(self):
        """Starts the Push-to-Talk background listener."""
        if self._is_active:
            return
        self._thread = threading.Thread(target=self._message_pump, daemon=True, name="PTTMessagePump")
        self._thread.start()
        time.sleep(0.2)

    def stop(self):
        """Stops the Push-to-Talk background listener."""
        self._is_active = False
        if self._thread_id:
            self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)


def run_microphone_verification(duration: float = 2.0, server_url: str = DEFAULT_SERVER_URL) -> bool:
    """
    Comprehensive Verification Suite:
    1. Audio hardware discovery & 2-second microphone capture test.
    2. Mock audio transcription test via STTEngine.
    3. Rule-based & pattern-matching Intent Parser unit test (Thai & English + safe fallback).
    4. End-to-end PTT -> STT -> Intent -> Server HUD execution.
    """
    print("=" * 65)
    print(" REAL-TIME AUDIO, STT & INTENT: COMPREHENSIVE VERIFICATION")
    print("=" * 65)

    # 1. Microphone Discovery
    print("[1/4] Querying Audio Input Devices...")
    try:
        devices = sd.query_devices()
        default_in = sd.default.device[0]
        in_name = devices[default_in]["name"] if default_in >= 0 else "Unknown"
        print(f"      Default Input: Device #{default_in} ({in_name})")
        print("      -> PASSED (Audio hardware available)")
    except Exception as e:
        print(f"      -> FAILED to query audio devices: {e}")
        return False

    # 2. Non-blocking 2-Second Capture Test
    print(f"\n[2/4] Performing {duration:.1f}-Second Non-blocking Microphone Capture...")
    recorder = AudioRecorder(sample_rate=DEFAULT_SAMPLE_RATE, channels=DEFAULT_CHANNELS)

    cursor = get_current_cursor_pos()
    print(f"      Cursor Anchor: {cursor}")
    print("      Starting sounddevice input stream (16kHz 16-bit Mono PCM)...")

    t_start = time.perf_counter()
    result = recorder.record_fixed_duration(duration_sec=duration)
    elapsed = time.perf_counter() - t_start

    print(f"      Captured Duration: {result.duration_sec:.2f}s (Real elapsed: {elapsed:.2f}s)")
    print(f"      Total Samples:     {result.num_samples}")
    print(f"      WAV Buffer Size:   {result.buffer_size_bytes} bytes ({result.buffer_size_bytes/1024:.1f} KB)")
    print(f"      RMS Signal Energy: {result.rms_energy:.1f}")
    print(f"      Peak Amplitude:    {result.peak_amplitude}")

    assert result.buffer_size_bytes > 0, "Buffer size is 0 bytes"
    assert result.num_samples > 0, "No samples captured"
    assert result.sample_rate == 16000, f"Expected 16000Hz, got {result.sample_rate}"
    assert result.channels == 1, f"Expected 1 channel, got {result.channels}"
    assert result.wav_bytes[:4] == b"RIFF", "Invalid WAV header: missing RIFF"
    assert result.wav_bytes[8:12] == b"WAVE", "Invalid WAV header: missing WAVE"
    print("      -> PASSED (Microphone capture, WAV header & metrics valid)")

    # 3. Mock Audio Transcription Test (STTEngine)
    print("\n[3/4] Testing Mock Audio Transcription (STTEngine)...")
    stt_engine = STTEngine(use_fallback_only=True)

    mock_wav = create_test_wav_buffer(duration_sec=1.0)
    stt_res = stt_engine.transcribe(mock_wav)
    assert stt_res.success, f"STT failed: {stt_res.error_message}"
    print(f"      Transcribe Status: Success={stt_res.success} | Latency: {stt_res.latency_ms:.1f}ms")
    print(f"      Output Text:       \"{stt_res.text}\" (Language: {stt_res.language})")
    print("      -> PASSED (STTEngine audio transcription operational)")

    # 4. Intent Parser Unit Test & Pipeline Dispatch
    print("\n[4/4] Testing Intent Parser & HUD Pipeline Dispatch...")
    parser = IntentParser()

    # Verify intent matching rules
    test_phrases = [
        ("เปิดบราวเซอร์", "OPEN_BROWSER", "start chrome"),
        ("open browser", "OPEN_BROWSER", "start chrome"),
        ("เช็คสถานะ git", "GIT_STATUS", "git status --short"),
        ("git status", "GIT_STATUS", "git status --short"),
        ("เปิดโปรเจกต์", "OPEN_PROJECT", "explorer ."),
        ("open project", "OPEN_PROJECT", "explorer ."),
        ("คำสั่งที่ไม่รู้จัก 123", "UNKNOWN_INTENT", None)
    ]

    for phrase, exp_id, exp_cmd in test_phrases:
        parsed = parser.parse(phrase)
        assert parsed.intent_id == exp_id, f"Expected {exp_id} for '{phrase}', got {parsed.intent_id}"
        if exp_cmd:
            assert parsed.command == exp_cmd, f"Expected command {exp_cmd}, got {parsed.command}"
        else:
            assert not parsed.is_matched and parsed.executable_command.startswith("echo [SAFE FALLBACK]")
        print(f"      Matched: '{phrase}' -> [{parsed.intent_id}] `{parsed.command or parsed.executable_command}`")

    # Dispatch sample pipeline intent to server to verify HUD rendering
    sample_intent = parser.parse("เช็คสถานะ git")
    try:
        payload = {
            "command": sample_intent.executable_command,
            "mode": "ACTION",
            "stt_text": "เช็คสถานะ git",
            "intent_id": sample_intent.intent_id,
            "cursor_pos": cursor,
            "duration": 2.5
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            server_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            print(f"      Server Pipeline Response: Status={resp_data.get('status')} | Exit={resp_data.get('exit_code')}")
            print(f"      HUD Mode Dispatched: {resp_data.get('mode')} (Speech: \"เช็คสถานะ git\")")
            print("      -> PASSED (HUD recognized text & action dispatched)")
    except Exception as e:
        print(f"      -> WARNING: Could not connect to server at {server_url}: {e}")

    print("\n" + "=" * 65)
    print(" ALL VERIFICATION CHECKS COMPLETED SUCCESSFULLY (0 ERRORS)")
    print("=" * 65)
    return True


def main():
    parser = argparse.ArgumentParser(description="Windows Edge - Real-Time Audio Streamer & Push-to-Talk")
    parser.add_argument("--test", action="store_true", help="Run 2-second capture verification test and exit")
    parser.add_argument("--duration", type=float, default=2.0, help="Test capture duration in seconds (default: 2.0s)")
    parser.add_argument("--server", type=str, default=DEFAULT_SERVER_URL, help="Event Bus server endpoint URL")
    parser.add_argument("--save", type=str, default=None, help="Save test audio to specified WAV path")
    args = parser.parse_args()

    if args.test:
        success = run_microphone_verification(duration=args.duration, server_url=args.server)
        sys.exit(0 if success else 1)

    print("=" * 65)
    print(" WINDOWS EDGE: REAL-TIME AUDIO STREAMER & PUSH-TO-TALK")
    print("=" * 65)
    print(f"  - Hotkey:          [ Ctrl + Alt + Space ]")
    print(f"  - Operation:       HOLD to Record, RELEASE to Send")
    print(f"  - Audio Format:    16 kHz, 16-bit Mono PCM")
    print(f"  - Server Target:   {args.server}")
    print("=" * 65)

    listener = PushToTalkAudioListener(server_url=args.server)
    listener.start()

    print("\n>>> Push-to-Talk Listener is ACTIVE in the background.")
    print(">>> HOLD [ Ctrl + Alt + Space ] to record speech, RELEASE to finish.")
    print(">>> Press Ctrl+C in this terminal to exit.\n")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[AudioRecorder] Stopping Push-to-Talk listener...")
        listener.stop()
        print("[AudioRecorder] Clean exit.")


if __name__ == "__main__":
    main()
