import asyncio
import io
from typing import Optional
import mss
from PIL import Image

class ScreenCaptureProvider:
    """
    Asynchronously captures desktop screens and compresses into in-memory JPEG bytes.
    """
    def __init__(self, quality: int = 80, monitor_index: int = 1):
        self._quality = quality
        self._monitor_index = monitor_index

    def _sync_capture(self) -> Optional[bytes]:
        try:
            with mss.mss() as sct:
                monitors = sct.monitors
                # Use specified monitor or fallback to primary
                target = monitors[self._monitor_index] if len(monitors) > self._monitor_index else monitors[0]
                sct_img = sct.grab(target)
                
                # Convert raw MSS BGRA/RGB or mocked dict to PIL Image
                if isinstance(sct_img, dict):
                    size = (sct_img.get("width", 0), sct_img.get("height", 0))
                    raw_rgb = sct_img.get("rgb", b"")
                else:
                    size = getattr(sct_img, "size", (getattr(sct_img, "width", 0), getattr(sct_img, "height", 0)))
                    raw_rgb = getattr(sct_img, "rgb", b"")

                img = Image.frombytes("RGB", size, raw_rgb)
                
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=self._quality)
                return buffer.getvalue()
        except Exception:
            return None

    async def capture(self) -> Optional[bytes]:
        """Runs the capture and compression inside a worker thread to keep asyncio non-blocking."""
        return await asyncio.to_thread(self._sync_capture)
