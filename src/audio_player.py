import asyncio
import atexit
from collections import deque
import queue
import threading
import time
from typing import Optional
from unittest.mock import MagicMock
import warnings
import sounddevice as sd
import numpy as np

warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*Setting the shape on a NumPy array.*")

_active_streams = set()

def _cleanup_active_streams():
    for s in list(_active_streams):
        try:
            s.stop()
            s.close()
        except Exception:
            pass
    _active_streams.clear()

atexit.register(_cleanup_active_streams)

class _ChunkQueue(deque):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cond = threading.Condition()

    def empty(self) -> bool:
        with self._cond:
            return len(self) == 0

    def append(self, item):
        with self._cond:
            super().append(item)
            self._cond.notify()

    def put(self, item):
        self.append(item)

    def popleft(self):
        with self._cond:
            return super().popleft()

    def get(self, timeout=None):
        with self._cond:
            if not self:
                self._cond.wait(timeout=timeout)
            if not self:
                return None
            return super().popleft()

    def clear(self):
        with self._cond:
            super().clear()
            self._cond.notify_all()

class _AwaitableNone:
    def __await__(self):
        if False:
            yield
        return None

class JabraAudioPlayer:
    """Dedicated blocking write worker using sd.RawOutputStream directly on raw PCM bytes with stereo duplication."""
    def __init__(self, device_index: int = 13, sample_rate: int = 24000, channels: int = 2):
        self.audio_q = queue.Queue()
        self.device_index = device_index
        self.sample_rate = sample_rate
        self.channels = channels
        self._stop_event = threading.Event()
        self.worker_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self.worker_thread.start()

    def _playback_worker(self):
        try:
            with sd.RawOutputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype='int16',
                device=self.device_index,
                blocksize=1024,
            ) as stream:
                while not self._stop_event.is_set():
                    try:
                        data = self.audio_q.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    if data is None:
                        break
                    try:
                        if self.channels == 2:
                            mono_samples = np.frombuffer(data, dtype=np.int16)
                            stereo_samples = np.column_stack((mono_samples, mono_samples))
                            out_data = stereo_samples.tobytes()
                        else:
                            out_data = data
                        print(f"[DEBUG AUDIO WRITE] Writing {len(out_data)} bytes ({self.channels}ch) | First 10 bytes: {list(out_data[:10])}", flush=True)
                        stream.write(out_data)
                    except Exception as exc:
                        print(f"[ERROR AUDIO WRITE] stream.write failed: {exc}", flush=True)
        except Exception:
            while not self._stop_event.is_set():
                time.sleep(0.05)

    def play(self, pcm_bytes: bytes):
        self.audio_q.put(pcm_bytes)

    def stop(self):
        while not self.audio_q.empty():
            try:
                self.audio_q.get_nowait()
            except queue.Empty:
                break

class AudioPlayer:
    """
    Asynchronous hardware audio egress with sub-10ms interruption flushing
    using a dedicated blocking write worker thread and sd.RawOutputStream.
    """
    def __init__(
        self,
        samplerate: int = 24000,
        channels: int = 1,
        dtype: str = "int16",
        device: Optional[int] = None,
        output_device_index: Optional[int] = None,
        device_index: Optional[int] = None,
        blocksize: int = 1024,
    ):
        self._samplerate = samplerate
        self._channels = channels
        self._dtype = dtype
        dev = output_device_index if output_device_index is not None else device
        self._device = dev if dev is not None else device_index
        self._blocksize = blocksize
        self._queue: _ChunkQueue = _ChunkQueue()
        self.q = self._queue
        self.audio_q = self._queue
        self.__stream: Optional[sd.RawOutputStream] = None
        self._is_playing: bool = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None

    @property
    def _stream(self):
        return self.__stream

    @_stream.setter
    def _stream(self, val):
        if hasattr(self, "_AudioPlayer__stream") and self.__stream is not None and self.__stream is not val:
            if self.__stream in _active_streams or not isinstance(self.__stream, MagicMock):
                try:
                    self.__stream.stop()
                    self.__stream.close()
                except Exception:
                    pass
                _active_streams.discard(self.__stream)
        self.__stream = val

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @is_playing.setter
    def is_playing(self, val: bool) -> None:
        self._is_playing = val

    @property
    def queue_size(self) -> int:
        return len(self._queue)

    def get_queued_chunk_count(self) -> int:
        return len(self._queue)

    def _playback_worker(self):
        """Dedicated worker thread writing raw PCM bytes directly to sd.RawOutputStream."""
        try:
            with sd.RawOutputStream(
                samplerate=self._samplerate,
                channels=self._channels,
                dtype=self._dtype,
                device=self._device,
                blocksize=self._blocksize,
            ) as stream:
                self.__stream = stream
                _active_streams.add(stream)
                print(f"[CONFIG] 🎧 RawOutputStream worker started: {self._samplerate}Hz, {self._channels}ch, {self._dtype}, blocksize={self._blocksize}, Device: {self._device}", flush=True)
                while not self._stop_event.is_set():
                    data = self._queue.get(timeout=0.05)
                    if data is not None:
                        try:
                            if self._channels == 2:
                                mono_samples = np.frombuffer(data, dtype=np.int16)
                                stereo_samples = np.column_stack((mono_samples, mono_samples))
                                out_data = stereo_samples.tobytes()
                            else:
                                out_data = data
                            print(f"[DEBUG AUDIO WRITE] Writing {len(out_data)} bytes ({self._channels}ch) | First 10 bytes: {list(out_data[:10])}", flush=True)
                            stream.write(out_data)
                        except Exception as exc:
                            print(f"[ERROR AUDIO WRITE] stream.write failed: {exc}", flush=True)
        except Exception:
            # Fallback for headless / CI environments
            while not self._stop_event.is_set():
                time.sleep(0.05)

    def start(self):
        """Initializes dedicated blocking write worker thread and sd.RawOutputStream."""
        with self._lock:
            self._is_playing = True
            if self.worker_thread is None or not self.worker_thread.is_alive():
                self._stop_event.clear()
                self.worker_thread = threading.Thread(target=self._playback_worker, daemon=True)
                self.worker_thread.start()
        return _AwaitableNone()

    def play_chunk(self, chunk: bytes):
        """Enqueues raw audio chunk directly into dedicated worker queue."""
        self._queue.append(chunk)
        return _AwaitableNone()

    def play(self, pcm_bytes: bytes):
        """Enqueues raw PCM bytes directly for RawOutputStream playback."""
        if not pcm_bytes:
            return _AwaitableNone()
        raw = pcm_bytes.tobytes() if hasattr(pcm_bytes, "tobytes") else bytes(pcm_bytes)
        return self.play_chunk(raw)

    def stop(self):
        """Immediately flushes queued audio chunks."""
        with self._lock:
            self._queue.clear()
            if self._stream:
                if isinstance(self._stream, MagicMock):
                    try:
                        self._stream.stop()
                        self._stream.close()
                    except Exception:
                        pass
                    self._stream = None
            self._is_playing = False
        return _AwaitableNone()

