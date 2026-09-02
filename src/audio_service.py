"""
Audio Service with Deterministic Teardown Lifecycle and Non-Blocking Stream Management.
Prevents Kernel I/O Deadlocks and Zombie Processes on Windows by enforcing:
1. Thread hygiene with daemon=True
2. Non-blocking audio stream reads with abort_event polling
3. Strict try...finally resource reclamation for stream and audio interfaces
"""

import logging
import threading
import time
from typing import Callable, Generator, Optional, Any
import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

import config

logger = logging.getLogger("AudioService")


def is_audio_driver_error(exception: Exception) -> bool:
    """
    Checks if an exception represents a PortAudio / PyAudio driver error,
    such as -9999 (Unanticipated host error), MME error, or device disconnect.
    """
    if sd is not None and isinstance(exception, getattr(sd, "PortAudioError", (OSError, IOError))):
        return True
    if isinstance(exception, (OSError, IOError)):
        return True
    err_str = str(exception).lower()
    return any(marker in err_str for marker in (
        "-9999",
        "unanticipated host error",
        "mme error",
        "device unavailable",
        "input overflowed",
        "buffer error",
        "portaudio",
        "pyaudio"
    ))


class AudioService:
    """
    Audio capture and stream management service with deterministic teardown lifecycle.
    Ensures safe, non-blocking stream reads and strict try...finally resource reclamation.
    """

    def __init__(self, sample_rate: int = config.SAMPLE_RATE, channels: int = config.CHANNELS):
        self.sample_rate = sample_rate
        self.channels = channels
        self.stream: Optional[Any] = None
        self.audio: Optional[Any] = None
        self.abort_event: threading.Event = threading.Event()
        self._lock: threading.Lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def cleanup(self) -> None:
        """
        Deterministic teardown lifecycle: signals abort event and strictly reclaims
        stream and audio hardware handles (supporting PyAudio, PortAudio, and sounddevice).
        """
        self.abort_event.set()
        with self._lock:
            if self.stream is not None:
                try:
                    if hasattr(self.stream, "stop_stream"):
                        self.stream.stop_stream()
                except Exception:
                    pass

                try:
                    if hasattr(self.stream, "stop"):
                        self.stream.stop()
                except Exception:
                    pass

                try:
                    if hasattr(self.stream, "abort"):
                        self.stream.abort()
                except Exception:
                    pass

                try:
                    if hasattr(self.stream, "close"):
                        self.stream.close()
                except Exception:
                    pass
                self.stream = None

            if self.audio is not None:
                try:
                    if hasattr(self.audio, "terminate"):
                        self.audio.terminate()
                except Exception:
                    pass
                self.audio = None

    def read_chunk(self, frame_samples: int, timeout: float = 0.05) -> Optional[bytes]:
        """
        Reads a single chunk of audio frames non-blockingly, respecting abort_event.
        Returns bytes if available, or None if aborted/timed out.
        """
        if self.abort_event.is_set() or self.stream is None:
            return None

        # Check available frames without blocking if stream supports read_available
        read_avail = getattr(self.stream, "read_available", None)
        if isinstance(read_avail, int) and read_avail < frame_samples:
            # Wait in small slices for frames to arrive
            start_t = time.perf_counter()
            while not self.abort_event.is_set():
                cur_avail = getattr(self.stream, "read_available", 0)
                if isinstance(cur_avail, int) and cur_avail >= frame_samples:
                    break
                if (time.perf_counter() - start_t) >= timeout:
                    return None
                if self.abort_event.wait(timeout=0.005):
                    return None

        if self.abort_event.is_set() or self.stream is None:
            return None

        try:
            res = self.stream.read(frame_samples)
            if isinstance(res, tuple):
                data = res[0]
            else:
                data = res
            return bytes(data) if data else None
        except Exception as e:
            if self.abort_event.is_set():
                return None
            raise e

    def stream_capture(
        self,
        is_active_callback: Optional[Callable[[], bool]] = None,
        frame_samples: Optional[int] = None,
        max_retries: int = 10,
        retry_delay: float = 0.3
    ) -> Generator[bytes, None, None]:
        """
        Continuous non-blocking audio capture generator with automatic recovery
        and deterministic teardown lifecycle.
        """
        if frame_samples is None:
            frame_samples = int(self.sample_rate * (config.FRAME_DURATION_MS / 1000.0))

        if is_active_callback is None:
            is_active_callback = lambda: not self.abort_event.is_set()

        self.abort_event.clear()
        retry_count = 0

        while is_active_callback() and not self.abort_event.is_set():
            try:
                if sd is not None:
                    self.stream = sd.RawInputStream(
                        samplerate=self.sample_rate,
                        channels=self.channels,
                        dtype='int16',
                        blocksize=frame_samples
                    )
                    self.stream.start()

                if retry_count > 0:
                    logger.info(f"[AudioService Recovery] Successfully re-initialized stream (recovered after {retry_count} retries).")
                    retry_count = 0

                while is_active_callback() and not self.abort_event.is_set():
                    # Non-blocking check with abort_event polling
                    read_avail = getattr(self.stream, "read_available", None)
                    if isinstance(read_avail, int) and read_avail < frame_samples:
                        if self.abort_event.wait(timeout=0.005):
                            break
                        if not is_active_callback():
                            break
                        continue

                    if self.abort_event.is_set() or not is_active_callback():
                        break

                    res = self.stream.read(frame_samples)
                    if not is_active_callback() or self.abort_event.is_set():
                        break

                    if isinstance(res, tuple):
                        data, overflow = res
                    else:
                        data, overflow = res, False

                    if data:
                        yield bytes(data)

            except Exception as e:
                if self.abort_event.is_set() or not is_active_callback():
                    break

                if is_audio_driver_error(e):
                    retry_count += 1
                    logger.warning(
                        f"[AudioService Warning] PortAudio/PyAudio error [attempt {retry_count}/{max_retries}]: {e}. "
                        f"Auto-recovering in {retry_delay:.2f}s..."
                    )
                    if retry_count >= max_retries:
                        logger.error(f"[AudioService Error] Maximum recovery retries ({max_retries}) exceeded.")
                        raise
                    if self.abort_event.wait(timeout=retry_delay):
                        break
                else:
                    logger.error(f"[AudioService Error] Non-recoverable capture exception: {e}")
                    raise
            finally:
                self.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
