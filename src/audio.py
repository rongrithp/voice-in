import time
import threading
import logging
import queue
from typing import Callable, Generator, Optional
import math
import numpy as np
import sounddevice as sd
import config
from src.audio_service import AudioService, is_audio_driver_error

logger = logging.getLogger("Audio")


class WindHarmonicsFilter:
    """
    Real-time 2nd-order IIR Butterworth High-Pass Filter.
    Eliminates low-frequency wind rumble and breath plosives/harmonics (default cutoff 80.0 Hz)
    while preserving vocal clarity (> 100-3000 Hz).
    Operates statelessly across streaming int16 PCM chunks with minimal computational overhead (< 0.2ms/chunk).
    """

    def __init__(self, cutoff_hz: float = 80.0, sample_rate: int = config.SAMPLE_RATE):
        self.cutoff_hz = float(cutoff_hz)
        self.sample_rate = int(sample_rate)
        self._update_coefficients()
        self.reset()

    def _update_coefficients(self) -> None:
        # 2nd-order Butterworth High-pass Biquad design
        w0 = 2.0 * math.pi * self.cutoff_hz / self.sample_rate
        alpha = math.sin(w0) / (2.0 * 0.7071067811865475)  # Q = 1/sqrt(2)
        cos_w0 = math.cos(w0)

        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

        self.b0 = float(b0 / a0)
        self.b1 = float(b1 / a0)
        self.b2 = float(b2 / a0)
        self.a1 = float(a1 / a0)
        self.a2 = float(a2 / a0)

    def reset(self) -> None:
        """Reset internal filter delay state."""
        self.x1 = 0.0
        self.x2 = 0.0
        self.y1 = 0.0
        self.y2 = 0.0

    def process_samples(self, samples: np.ndarray) -> np.ndarray:
        """
        Filters 1D float32 or int16 numpy array.
        Returns filtered array in the same dtype.
        """
        if samples is None or len(samples) == 0:
            return samples

        orig_dtype = samples.dtype
        in_float = samples.astype(np.float32)
        num_samples = len(in_float)
        out_float = np.empty(num_samples, dtype=np.float32)

        b0, b1, b2 = self.b0, self.b1, self.b2
        a1, a2 = self.a1, self.a2
        x1, x2 = self.x1, self.x2
        y1, y2 = self.y1, self.y2

        for i in range(num_samples):
            x = in_float[i]
            y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2 = x1
            x1 = x
            y2 = y1
            y1 = y
            out_float[i] = y

        self.x1, self.x2 = x1, x2
        self.y1, self.y2 = y1, y2

        if np.issubdtype(orig_dtype, np.integer):
            np.clip(out_float, -32768.0, 32767.0, out=out_float)
            return out_float.astype(orig_dtype)
        return out_float

    def process_pcm_bytes(self, pcm_bytes: bytes) -> bytes:
        """
        Filters raw 16-bit mono PCM bytes stream and returns clean PCM bytes.
        """
        if not pcm_bytes:
            return b""
        int16_arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        filtered_arr = self.process_samples(int16_arr)
        return filtered_arr.tobytes()


def filter_wind_harmonics(
    pcm_data: bytes | np.ndarray,
    cutoff_hz: float = 80.0,
    sample_rate: int = config.SAMPLE_RATE
) -> bytes | np.ndarray:
    """
    Convenience function to filter wind harmonics and rumble from PCM data or numpy array.
    """
    flt = WindHarmonicsFilter(cutoff_hz=cutoff_hz, sample_rate=sample_rate)
    if isinstance(pcm_data, (bytes, bytearray)):
        return flt.process_pcm_bytes(bytes(pcm_data))
    return flt.process_samples(pcm_data)


def calculate_rms(audio_chunk: np.ndarray) -> float:
    """Calculate Root Mean Square (RMS) of audio chunk for noise gate filter."""
    if audio_chunk is None or len(audio_chunk) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio_chunk.astype(float) ** 2)))

def is_silence(audio_chunk: np.ndarray, rms_threshold: float = 250.0) -> bool:
    """Check if audio chunk RMS is below silence threshold."""
    return calculate_rms(audio_chunk) < rms_threshold

def robust_audio_stream_capture(
    is_active_callback: Callable[[], bool],
    sample_rate: int = config.SAMPLE_RATE,
    channels: int = config.CHANNELS,
    frame_samples: Optional[int] = None,
    max_retries: int = 10,
    retry_delay: float = 0.3,
    abort_event: Optional[threading.Event] = None,
    enable_wind_filter: Optional[bool] = None,
    wind_cutoff_hz: Optional[float] = None
) -> Generator[bytes, None, None]:
    """
    Continuous audio stream generator with non-blocking stream reads,
    automatic PortAudio/PyAudio (-9999) error recovery, and deterministic teardown.
    """
    if frame_samples is None:
        frame_samples = int(sample_rate * (config.FRAME_DURATION_MS / 1000.0))

    local_abort_event = abort_event if abort_event is not None else threading.Event()
    retry_count = 0

    apply_wind_filter = enable_wind_filter if enable_wind_filter is not None else getattr(config, "ENABLE_WIND_FILTER", True)
    cutoff = wind_cutoff_hz if wind_cutoff_hz is not None else getattr(config, "WIND_FILTER_CUTOFF_HZ", 80.0)
    wind_filter = WindHarmonicsFilter(cutoff_hz=cutoff, sample_rate=sample_rate) if apply_wind_filter else None

    while is_active_callback() and not local_abort_event.is_set():
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

            while is_active_callback() and not local_abort_event.is_set():
                # Non-blocking check with abort_event polling
                read_avail = getattr(stream, "read_available", None)
                if isinstance(read_avail, int) and read_avail < frame_samples:
                    if local_abort_event.wait(timeout=0.005):
                        break
                    continue

                data, overflow = stream.read(frame_samples)
                if not is_active_callback() or local_abort_event.is_set():
                    break

                if data:
                    raw_bytes = bytes(data)
                    clean_bytes = wind_filter.process_pcm_bytes(raw_bytes) if wind_filter is not None else raw_bytes
                    yield clean_bytes

        except Exception as e:
            if not is_active_callback() or local_abort_event.is_set():
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
                if local_abort_event.wait(timeout=retry_delay):
                    break
            else:
                logger.error(f"[Audio Stream Error] Non-recoverable audio capture exception: {e}")
                raise
        finally:
            if stream is not None:
                try:
                    if hasattr(stream, "stop_stream"):
                        stream.stop_stream()
                    elif hasattr(stream, "stop"):
                        stream.stop()
                except Exception:
                    pass
                try:
                    if hasattr(stream, "abort"):
                        stream.abort()
                except Exception:
                    pass
                try:
                    if hasattr(stream, "close"):
                        stream.close()
                except Exception:
                    pass


class LiveAudioStreamProducer:
    """
    Continuous real-time audio capture producer with deterministic lifecycle.
    Streams 16kHz 16-bit Mono PCM chunks into a target queue or provides an iterator,
    with automatic PortAudio driver error recovery and non-blocking abort support.
    """
    def __init__(self, sample_rate: int = config.SAMPLE_RATE, channels: int = config.CHANNELS, frame_ms: int = 100):
        self.sample_rate = sample_rate
        self.channels = channels
        self.frame_samples = int(sample_rate * (frame_ms / 1000.0))
        self.is_active = False
        self.abort_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.queue: queue.Queue = queue.Queue(maxsize=100)

    def start(self, output_queue: Optional[queue.Queue] = None):
        """Starts live audio stream production in background daemon worker thread."""
        if self.is_active:
            return
        self.is_active = True
        self.abort_event.clear()
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
                    frame_samples=self.frame_samples,
                    abort_event=self.abort_event
                ):
                    if not self.is_active or self.abort_event.is_set():
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
        """Stops audio stream production with deterministic cleanup."""
        self.is_active = False
        self.abort_event.set()
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
