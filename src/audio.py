import time
import threading
import logging
import queue
from typing import Callable, Generator, Optional
import numpy as np
import sounddevice as sd
import config

logger = logging.getLogger("Audio")

def calculate_rms(audio_chunk: np.ndarray) -> float:
    """Calculate Root Mean Square (RMS) of audio chunk for noise gate filter."""
    if audio_chunk is None or len(audio_chunk) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio_chunk.astype(float) ** 2)))

def is_silence(audio_chunk: np.ndarray, rms_threshold: float = 250.0) -> bool:
    """Check if audio chunk RMS is below silence threshold."""
    return calculate_rms(audio_chunk) < rms_threshold

def is_audio_driver_error(exception: Exception) -> bool:
    """
    Checks if an exception represents a PortAudio / PyAudio driver error,
    such as -9999 (Unanticipated host error), MME error, or device disconnect.
    """
    if isinstance(exception, (sd.PortAudioError, OSError, IOError)):
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

def robust_audio_stream_capture(
    is_active_callback: Callable[[], bool],
    sample_rate: int = config.SAMPLE_RATE,
    channels: int = config.CHANNELS,
    frame_samples: Optional[int] = None,
    max_retries: int = 10,
    retry_delay: float = 0.3
) -> Generator[bytes, None, None]:
    """
    Continuous audio stream generator with automatic PortAudio/PyAudio (-9999) error recovery.
    Re-initializes the microphone input stream automatically if driver disconnects or buffer error occurs.
    """
    if frame_samples is None:
        frame_samples = int(sample_rate * (config.FRAME_DURATION_MS / 1000.0))

    retry_count = 0
    while is_active_callback():
        stream = None
        try:
            stream = sd.RawInputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype='int16',
                blocksize=frame_samples
            )
            stream.start()

            if retry_count > 0:
                logger.info(f"[Audio Recovery] Successfully re-initialized audio input stream (recovered after {retry_count} retries).")
                retry_count = 0

            while is_active_callback():
                data, overflow = stream.read(frame_samples)
                if not is_active_callback():
                    break
                if data and not overflow:
                    yield bytes(data)
                elif overflow and data:
                    # Minor buffer overflow; keep streaming available frames
                    yield bytes(data)

        except Exception as e:
            if not is_active_callback():
                break

            if is_audio_driver_error(e):
                retry_count += 1
                logger.warning(
                    f"[Audio Stream Warning] Caught PortAudio/PyAudio error (code -9999 / driver error) [attempt {retry_count}/{max_retries}]: {e}. "
                    f"Auto-recovering in {retry_delay:.2f}s..."
                )
                if retry_count >= max_retries:
                    logger.error(f"[Audio Stream Error] Maximum recovery retries ({max_retries}) exceeded.")
                    raise
                time.sleep(retry_delay)
            else:
                logger.error(f"[Audio Stream Error] Non-recoverable audio capture exception: {e}")
                raise
        finally:
            if stream is not None:
                try:
                    stream.stop()
                except Exception:
                    pass
                try:
                    stream.close()
                except Exception:
                    pass

class LiveAudioStreamProducer:
    """
    Continuous real-time audio capture producer.
    Streams 16kHz 16-bit Mono PCM chunks into a target queue or provides an iterator,
    with automatic PortAudio driver error recovery.
    """
    def __init__(self, sample_rate: int = config.SAMPLE_RATE, channels: int = config.CHANNELS, frame_ms: int = 100):
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_samples = int(sample_rate * (frame_ms / 1000.0))
        self.is_active = False
        self._thread: Optional[threading.Thread] = None
        self.queue: queue.Queue = queue.Queue(maxsize=100)

    def start(self, output_queue: Optional[queue.Queue] = None):
        """Starts live audio stream production in background worker thread."""
        if self.is_active:
            return
        self.is_active = True
        if output_queue is not None:
            self.queue = output_queue
        else:
            with self.queue.mutex:
                self.queue.queue.clear()

        def _producer_loop():
            try:
                for chunk in robust_audio_stream_capture(
                    is_active_callback=lambda: self.is_active,
                    sample_rate=self.sample_rate,
                    channels=self.channels,
                    frame_samples=self.frame_samples
                ):
                    if not self.is_active:
                        break
                    try:
                        self.queue.put_nowait(chunk)
                    except queue.Full:
                        try:
                            _ = self.queue.get_nowait()
                        except queue.Empty:
                            pass
                        self.queue.put_nowait(chunk)
            except Exception as e:
                logger.error(f"[LiveAudioProducer Error] Stream failed: {e}")
            finally:
                self.is_active = False

        self._thread = threading.Thread(target=_producer_loop, daemon=True, name="LiveAudioProducerThread")
        self._thread.start()

    def stop(self) -> None:
        """Stops audio stream production."""
        self.is_active = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None


class AudioBuffer:
    """Non-blocking queue manager for audio streaming with drop-oldest strategy."""

    def __init__(self, maxsize: int = 10):
        self.maxsize = maxsize
        self.queue = queue.Queue(maxsize=maxsize)

    def push(self, data: np.ndarray) -> bool:
        """Push chunk to queue. If full, drop the oldest chunk to preserve real-time latency."""
        try:
            self.queue.put_nowait(data)
            return True
        except queue.Full:
            try:
                _ = self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(data)
                return True
            except queue.Full:
                return False

    def get(self, timeout: float = 0.5):
        """Retrieve next item from queue."""
        return self.queue.get(timeout=timeout)

    def task_done(self):
        """Mark current task as complete."""
        self.queue.task_done()

    def clear(self):
        """Clear all items in queue."""
        with self.queue.mutex:
            self.queue.queue.clear()
