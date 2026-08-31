import io
import logging
from PIL import Image
import mss

logger = logging.getLogger("ScreenCapture")

def get_physical_monitors() -> list[dict]:
    """
    Returns a list of physical monitor metadata dicts (excluding virtual Monitor 0).
    """
    try:
        with mss.MSS() as sct:
            monitors = sct.monitors
            return [
                {
                    "index": i,
                    "name": f"Monitor {i}" + (" (Primary)" if i == 1 else ""),
                    "width": mon["width"],
                    "height": mon["height"],
                    "left": mon["left"],
                    "top": mon["top"]
                }
                for i, mon in enumerate(monitors[1:], start=1)
            ]
    except Exception as e:
        logger.warning(f"[ScreenCapture] Could not list physical monitors: {e}")
        return []

def grab_monitor_thumbnail(monitor_index: int, max_width: int = 200, max_height: int = 120) -> Image.Image | None:
    """
    Captures a physical monitor and returns a downscaled PIL Image thumbnail.
    """
    try:
        with mss.MSS() as sct:
            monitors = sct.monitors
            if 1 <= monitor_index < len(monitors):
                target_mon = monitors[monitor_index]
                sct_img = sct.grab(target_mon)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                img.thumbnail((max_width, max_height), Image.Resampling.BILINEAR)
                return img
    except Exception as e:
        logger.debug(f"[ScreenCapture] Thumbnail capture failed for Mon {monitor_index}: {e}")
    return None

def log_detected_monitors():
    """
    Enumerates all connected physical displays and logs their ID, resolution, and offset coordinates.
    """
    try:
        with mss.MSS() as sct:
            monitors = sct.monitors
            num_monitors = len(monitors) - 1
            logger.info(f"[ScreenCapture] Detected {num_monitors} physical monitor(s):")
            for idx, mon in enumerate(monitors):
                if idx == 0:
                    logger.info(f"  • Monitor 0 (Virtual All-in-One): {mon['width']}x{mon['height']} at ({mon['left']}, {mon['top']})")
                else:
                    is_primary = " (Primary)" if idx == 1 else ""
                    logger.info(f"  • Monitor {idx}{is_primary}: {mon['width']}x{mon['height']} at ({mon['left']}, {mon['top']})")
    except Exception as e:
        logger.warning(f"[ScreenCapture Warning] Failed to enumerate monitors: {e}")

def copy_image_to_clipboard(image: Image.Image) -> bool:
    """
    Copies a PIL Image directly to the Windows OS clipboard as CF_DIB.
    """
    try:
        import win32clipboard
        output = io.BytesIO()
        image.convert("RGB").save(output, "BMP")
        data = output.getvalue()[14:]  # Strip 14-byte BMP header to obtain pure DIB structure
        output.close()

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
            logger.info(f"[ScreenCapture] Successfully copied image ({image.width}x{image.height}) to Windows clipboard.")
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        logger.error(f"[ScreenCapture Error] Failed to copy image to clipboard: {e}")
        return False

def capture_monitor_to_clipboard(monitor_index: int) -> bool:
    """
    Captures the specified physical monitor index (1-based: 1 for Mon1, 2 for Mon2, etc.)
    and copies it directly to the Windows clipboard.
    """
    try:
        with mss.MSS() as sct:
            monitors = sct.monitors
            # monitors[0] is virtual all-in-one bounding box, monitors[1] is Monitor 1, monitors[2] is Monitor 2, etc.
            if monitor_index < len(monitors):
                target_mon = monitors[monitor_index]
                sct_img = sct.grab(target_mon)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                logger.info(f"[ScreenCapture] Captured Monitor {monitor_index} ({target_mon['width']}x{target_mon['height']} at ({target_mon['left']}, {target_mon['top']}))")
                return copy_image_to_clipboard(img)
            else:
                logger.warning(f"[ScreenCapture Warning] Monitor {monitor_index} not found (Total monitors detected: {len(monitors)-1})")
                return False
    except Exception as e:
        logger.error(f"[ScreenCapture Error] Screen capture failed for monitor {monitor_index}: {e}")
        return False
