import asyncio
import threading
from collections import deque
from typing import List, Optional

class AudioCaptureBuffer:
    """
    Thread-safe, bounded, non-blocking audio chunk buffer with FIFO drop policy.
    """
    def __init__(self, max_chunks: int = 100):
        self._max_chunks = max_chunks
        self._deque: deque[bytes] = deque(maxlen=max_chunks)
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_chunks)

    @property
    def audio_queue(self) -> asyncio.Queue:
        return self._queue

    @property
    def is_empty(self) -> bool:
        return len(self._deque) == 0

    @property
    def chunk_count(self) -> int:
        return len(self._deque)

    def put_nowait(self, chunk: bytes) -> None:
        """Non-blocking put for audio thread."""
        self._deque.append(chunk)
        try:
            self._queue.put_nowait(chunk)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(chunk)
            except Exception:
                pass

    async def put(self, chunk: bytes) -> None:
        """Asynchronously append an audio chunk. Drops oldest automatically if full."""
        async with self._lock:
            self.put_nowait(chunk)

    def put_threadsafe(self, chunk: bytes, loop: asyncio.AbstractEventLoop) -> None:
        """Synchronously enqueue audio chunks from external audio thread callback."""
        loop.call_soon_threadsafe(self.put_nowait, chunk)

    async def get(self) -> Optional[bytes]:
        """Pops and returns the oldest audio chunk in FIFO order."""
        async with self._lock:
            if not self._deque:
                return None
            try:
                self._queue.get_nowait()
            except Exception:
                pass
            return self._deque.popleft()

    async def drain_all(self) -> List[bytes]:
        """Flushes and returns all accumulated chunks in order."""
        async with self._lock:
            items = list(self._deque)
            self._deque.clear()
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except Exception:
                    break
            return items

    def clear(self) -> None:
        """Immediately purges all buffered chunks."""
        self._deque.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Exception:
                break


class AudioRingBuffer:
    """
    Thread-safe ring buffer for audio chunks with bounded capacity and FIFO drop policy.
    """
    def __init__(self, capacity: int = 100):
        self._capacity = capacity
        self._deque: deque[bytes] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def put(self, chunk: bytes) -> None:
        """Appends an audio chunk, dropping the oldest chunk if capacity is exceeded."""
        with self._lock:
            self._deque.append(chunk)

    def size(self) -> int:
        """Returns the current number of chunks in the buffer."""
        with self._lock:
            return len(self._deque)

    def drain(self) -> List[bytes]:
        """Flushes and returns all accumulated chunks in FIFO order."""
        with self._lock:
            items = list(self._deque)
            self._deque.clear()
            return items

    def clear(self) -> None:
        """Purges all chunks from the buffer."""
        with self._lock:
            self._deque.clear()
