import queue
import threading
import time
import winsound
import sounddevice as sd
import numpy as np
import keyboard
from pynput import keyboard as pynput_keyboard
import config
from src.sanitizer import sanitize_text, reset_dedup_memory
from src.router import TranscribeEngine
from src.vad import WebRTCVADSegmenter
from src.actuator import inject_to_cursor

class VoiceInjectorApp:
    def __init__(self):
        self.is_streaming = False
        self.audio_queue = queue.Queue(maxsize=config.MAX_QUEUE_SIZE)
        self.engine = TranscribeEngine()
        self.vad_segmenter = WebRTCVADSegmenter()
        self.lock = threading.Lock()
        self.last_hotkey_time = 0.0

    def sound_feedback(self, freq: int, ms: int):
        threading.Thread(target=winsound.Beep, args=(freq, ms), daemon=True).start()

    def on_hotkey_pressed(self):
        now = time.time()
        if now - self.last_hotkey_time < 0.3:
            return
        self.last_hotkey_time = now
        print(f"\n[Key Detected] Hotkey [{config.HOTKEY.upper()}] pressed!", flush=True)
        self.toggle()

    def audio_worker(self):
        while self.is_streaming:
            try:
                segment = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            duration = len(segment) / config.SAMPLE_RATE
            print(f"[VAD Cut @ {config.VAD_SILENCE_MS}ms Silence] Transcribing {duration:.2f}s audio...", flush=True)

            raw_text = self.engine.transcribe(segment)
            if raw_text:
                clean_text = sanitize_text(raw_text)
                if clean_text:
                    print(f"[Transcribed]: {clean_text}", flush=True)
                    inject_to_cursor(clean_text)
            else:
                print("[Warning] Model returned empty text", flush=True)

            self.audio_queue.task_done()

    def stream_capture(self):
        # 30ms PCM frame = 480 samples @ 16kHz mono int16 (960 bytes)
        frame_samples = int(config.SAMPLE_RATE * (config.FRAME_DURATION_MS / 1000.0))
        frame_bytes_len = frame_samples * 2

        with sd.RawInputStream(samplerate=config.SAMPLE_RATE, channels=config.CHANNELS, dtype='int16') as stream:
            while self.is_streaming:
                data, overflow = stream.read(frame_samples)
                if not overflow and self.is_streaming and data:
                    completed_segments = self.vad_segmenter.process_pcm_chunk(bytes(data))
                    for segment in completed_segments:
                        try:
                            self.audio_queue.put_nowait(segment)
                        except queue.Full:
                            try:
                                _ = self.audio_queue.get_nowait()
                            except queue.Empty:
                                pass
                            self.audio_queue.put_nowait(segment)

    def toggle(self):
        with self.lock:
            if not self.is_streaming:
                self.is_streaming = True
                reset_dedup_memory()
                self.vad_segmenter.reset()
                self.sound_feedback(880, 80)
                print(f"[Status] 🟢 Local faster-whisper + WebRTC VAD Active... (Press {config.HOTKEY.upper()} to Stop)", flush=True)
                
                with self.audio_queue.mutex:
                    self.audio_queue.queue.clear()

                threading.Thread(target=self.audio_worker, daemon=True).start()
                threading.Thread(target=self.stream_capture, daemon=True).start()
            else:
                self.is_streaming = False
                # Flush any remaining voiced frames upon stopping
                flushed_segment = self.vad_segmenter.flush()
                if flushed_segment is not None and len(flushed_segment) > 0:
                    try:
                        self.audio_queue.put_nowait(flushed_segment)
                    except queue.Full:
                        pass
                self.sound_feedback(440, 120)
                print(f"[Status] ⏹️ Stopped.", flush=True)

    def run(self):
        print("==================================================")
        print(f" Local Voice-to-Cursor Daemon | Key: [{config.HOTKEY.upper()}]")
        print(f" Model: {config.MODEL_SIZE} | Device: {config.DEVICE} ({config.COMPUTE_TYPE}, {config.CPU_THREADS} threads)")
        print(f" VAD Cut: {config.VAD_SILENCE_MS}ms silence | Lang: '{config.LANGUAGE}'")
        print("==================================================")

        # Primary Listener (keyboard)
        try:
            keyboard.add_hotkey(config.HOTKEY, self.on_hotkey_pressed)
        except Exception as e:
            print(f"[Warning] Failed to register primary keyboard listener: {e}", flush=True)

        # Fallback Listener (pynput.keyboard.GlobalHotKeys)
        try:
            hotkey_str = config.HOTKEY if config.HOTKEY.startswith("<") else f"<{config.HOTKEY.lower()}>"
            pynput_listener = pynput_keyboard.GlobalHotKeys({
                hotkey_str: self.on_hotkey_pressed
            })
            pynput_listener.start()
            print(f"[Fallback Listener] pynput GlobalHotKeys active for {hotkey_str}", flush=True)
        except Exception as e:
            print(f"[Warning] Failed to register pynput fallback listener: {e}", flush=True)

        print(f"[Ready] Press [{config.HOTKEY.upper()}] to toggle streaming.", flush=True)
        keyboard.wait()



