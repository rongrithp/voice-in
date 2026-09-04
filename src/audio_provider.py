import asyncio
from typing import Optional, Callable, Any
import sounddevice as sd
from src.audio_buffer import AudioCaptureBuffer

class AudioCaptureProvider:
    """
    Hardware input provider that captures mic stream via sounddevice
    and pipes raw chunks directly into AudioCaptureBuffer.
    """
    def __init__(
        self,
        buffer: AudioCaptureBuffer,
        samplerate: int = 16000,
        channels: int = 1,
        dtype: str = "int16",
        blocksize: int = 1024,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        on_error: Optional[Callable[[Exception], Any]] = None,
        device: Optional[int] = None,
        persistent: bool = False,
    ):
        self._buffer = buffer
        self._samplerate = samplerate
        self._channels = channels
        self._dtype = dtype
        self._blocksize = blocksize
        self._loop = loop
        self._on_error = on_error
        self._device = device
        self._persistent = persistent
        self._stream: Optional[sd.InputStream] = None
        self._is_capturing: bool = False
        self._is_recording: bool = True

    @property
    def is_capturing(self) -> bool:
        return self._is_capturing

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @is_recording.setter
    def is_recording(self, value: bool) -> None:
        self._is_recording = value

    def _resolve_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop and self._loop.is_running():
            return self._loop
        return asyncio.get_event_loop()

    def mic_callback(self, indata, frames, time_info, status) -> None:
        """Executed inside the PortAudio C-thread. Non-blocking push only."""
        if not self._is_recording:
            return  # แฟล็กนี้ถูกตั้งเป็น True ตอนกด F20 PRESSED และ False ตอน F20 RELEASED

        if status:
            pass

        raw_bytes = bytes(indata)
        target_loop = self._resolve_loop()
        self._buffer.put_threadsafe(raw_bytes, target_loop)

    _audio_callback = mic_callback

    def start(self) -> bool:
        """Starts the audio recording stream or gates active stream."""
        self._is_recording = True
        if self._is_capturing and self._stream:
            try:
                if self._stream.stopped:
                    self._stream.start()
            except Exception:
                pass
            return True

        try:
            kwargs = {
                "samplerate": self._samplerate,
                "channels": self._channels,
                "dtype": self._dtype,
                "blocksize": self._blocksize,
                "callback": self._audio_callback
            }
            if self._device is not None:
                kwargs["device"] = self._device
            self._stream = sd.InputStream(**kwargs)
            self._stream.start()
            self._is_capturing = True
            return True
        except Exception as exc:
            self._is_capturing = False
            self._stream = None
            if self._on_error:
                target_loop = self._resolve_loop()
                asyncio.run_coroutine_threadsafe(self._on_error(exc), target_loop)
            return False

    def stop(self) -> None:
        """Stops the audio recording stream or gates persistent stream (Half-Duplex Guard)."""
        self._is_recording = False
        if self._persistent:
            if self._stream:
                try:
                    if self._stream.active:
                        self._stream.stop()
                except Exception:
                    pass
            return

        if not self._is_capturing and not self._stream:
            return

        try:
            if self._stream:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        finally:
            self._stream = None
            self._is_capturing = False

    def close(self) -> None:
        """Forcibly closes underlying hardware stream."""
        self._is_recording = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None
        self._is_capturing = False
