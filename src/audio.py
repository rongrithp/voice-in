import queue
import numpy as np

def calculate_rms(audio_chunk: np.ndarray) -> float:
    """Calculate Root Mean Square (RMS) of audio chunk for noise gate filter."""
    if audio_chunk is None or len(audio_chunk) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio_chunk.astype(float) ** 2)))

def is_silence(audio_chunk: np.ndarray, rms_threshold: float = 250.0) -> bool:
    """Check if audio chunk RMS is below silence threshold."""
    return calculate_rms(audio_chunk) < rms_threshold

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
