import io
import logging
from typing import Optional
from PIL import Image
import mss

logger = logging.getLogger("ScreenCapture")


def get_ordered_physical_monitors(sct: mss.MSS) -> list[dict]:
    """
    Returns an ordered list of physical monitors where Monitor 1 is ALWAYS the
    Primary Master Display (UltraWide 3440x1440 at coordinate 0,0).
    Excludes the virtual combined bounding box (index 0).
    """
    raw_monitors = list(sct.monitors[1:])
    if not raw_monitors:
        return []

    def _priority_key(m):
        # UltraWide 3440x1440 at 0,0 or Primary display gets top priority (Rank 0)
        is_uw_master = (m.get("width") == 3440 and m.get("height") == 1440) or (m.get("left") == 0 and m.get("top") == 0)
        is_pri = bool(m.get("is_primary", False)) or is_uw_master
        # Rank 0 for master/primary, then order by top then left
        return (0 if is_pri else 1, m.get("top", 0), m.get("left", 0))

    ordered = sorted(raw_monitors, key=_priority_key)
    return ordered


def get_monitor_dict(monitor_index: int, sct: Optional[mss.MSS] = None) -> dict | None:
    """
    Returns the mss monitor dict for the given 1-based physical monitor index
    under the remapped ordering (where Monitor 1 is UltraWide 3440x1440 at 0,0).
    """
    if sct is not None:
        ordered = get_ordered_physical_monitors(sct)
        if 1 <= monitor_index <= len(ordered):
            return ordered[monitor_index - 1]
        return None

    try:
        with mss.MSS() as local_sct:
            ordered = get_ordered_physical_monitors(local_sct)
            if 1 <= monitor_index <= len(ordered):
                return ordered[monitor_index - 1]
    except Exception as e:
        logger.warning(f"[ScreenCapture] get_monitor_dict error: {e}")
    return None


def get_physical_monitors() -> list[dict]:
    """
    Returns a list of physical monitor metadata dicts remapped so that Monitor 1
    is always the UltraWide 3440x1440 Primary Master Display.
    """
    try:
        with mss.MSS() as sct:
            ordered = get_ordered_physical_monitors(sct)
            return [
                {
                    "index": i,
                    "name": f"Monitor {i}" + (" (Primary UltraWide 3440x1440)" if i == 1 else ""),
                    "width": mon["width"],
                    "height": mon["height"],
                    "left": mon["left"],
                    "top": mon["top"]
                }
                for i, mon in enumerate(ordered, start=1)
            ]
    except Exception as e:
        logger.warning(f"[ScreenCapture] Could not list physical monitors: {e}")
        return []


def grab_monitor_thumbnail(monitor_index: int, max_width: int = 200, max_height: int = 120) -> Image.Image | None:
    """
    Captures a physical monitor under remapped ordering and returns a downscaled PIL Image thumbnail.
    """
    try:
        with mss.MSS() as sct:
            target_mon = get_monitor_dict(monitor_index, sct)
            if target_mon:
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
            ordered = get_ordered_physical_monitors(sct)
            logger.info(f"[ScreenCapture] Detected {len(ordered)} physical monitor(s) (Remapped UltraWide Master = Mon 1):")
            for idx, mon in enumerate(ordered, start=1):
                is_primary = " (Primary UltraWide)" if idx == 1 else ""
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
    Captures the specified physical monitor index (1-based: 1 for Monitor 1 UltraWide, 2 for Monitor 2, etc.)
    and copies it directly to the Windows clipboard.
    """
    try:
        with mss.MSS() as sct:
            target_mon = get_monitor_dict(monitor_index, sct)
            if target_mon:
                sct_img = sct.grab(target_mon)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                logger.info(f"[ScreenCapture] Captured Monitor {monitor_index} ({target_mon['width']}x{target_mon['height']} at ({target_mon['left']}, {target_mon['top']}))")
                return copy_image_to_clipboard(img)
            else:
                logger.warning(f"[ScreenCapture Warning] Monitor {monitor_index} not found in remapped monitor map.")
                return False
    except Exception as e:
        logger.error(f"[ScreenCapture Error] Screen capture failed for monitor {monitor_index}: {e}")
        return False
