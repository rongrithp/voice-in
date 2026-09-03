"""
fovea_capture.py — Mouse Fovea Vision Pipeline
===============================================

Implements a cursor-centric "fovea" capture pipeline that treats the mouse
cursor as the user's focal gaze point.  A 1280×720 bounding box is centred
on the cursor, clamped inside the active monitor's bounds, and a lightweight
semi-transparent red gaze reticle is drawn at the relative cursor position
inside the crop before JPEG encoding.

Key design invariants
---------------------
* Sampling rate: 15–20 Hz (controlled externally via frame interval).
* Debounce: bbox recomputed only when cursor moves ≥ move_threshold pixels.
* Thread-safe: all state in instance attributes; no module-level mutable state.
* No OpenCV dependency — uses Pillow/mss only.
"""

from __future__ import annotations

import ctypes
import io
import logging
import math
from typing import NamedTuple, Optional, Tuple

try:
    import mss
except ImportError:
    mss = None  # type: ignore
from PIL import Image, ImageDraw

logger = logging.getLogger("FoveaCapture")


# ── Lightweight geometry types ────────────────────────────────────────────────

class Point(NamedTuple):
    x: int
    y: int


class Rect(NamedTuple):
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


# ── Win32 cursor helper ───────────────────────────────────────────────────────

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _get_cursor_pos() -> Point:
    """Returns the current cursor position in virtual screen coordinates."""
    pt = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return Point(pt.x, pt.y)


# ── Core pipeline ─────────────────────────────────────────────────────────────

class FoveaCapturePipeline:
    """
    Cursor-centric screen capture pipeline.

    Usage
    -----
    Instantiate once per session.  On each vision tick, call
    ``capture_fovea_frame(sct)`` — it returns raw JPEG bytes ready for
    ``types.Blob(data=..., mime_type="image/jpeg")``.

    Parameters
    ----------
    fovea_width:      Width of the capture crop in pixels  (default 1280).
    fovea_height:     Height of the capture crop in pixels (default 720).
    move_threshold:   Minimum cursor displacement in pixels before the
                      bounding box is recomputed (default 20).
    reticle_radius:   Radius of the gaze indicator circle in pixels (default 10).
    reticle_color:    RGBA colour tuple for the reticle fill (default semi-
                      transparent red: (220, 30, 30, 160)).
    """

    def __init__(
        self,
        fovea_width: int = 1280,
        fovea_height: int = 720,
        move_threshold: int = 20,
        reticle_radius: int = 10,
        reticle_color: Tuple[int, int, int, int] = (220, 30, 30, 160),
    ) -> None:
        self.fovea_width = fovea_width
        self.fovea_height = fovea_height
        self.move_threshold = move_threshold
        self.reticle_radius = reticle_radius
        self.reticle_color = reticle_color

        self._last_cursor: Optional[Point] = None
        self._last_bbox: Optional[Rect] = None
        self._last_monitor: Optional[dict] = None
        self._last_rel_cursor: Optional[Point] = None  # cursor inside crop

    # ── Public API ────────────────────────────────────────────────────────────

    def get_active_monitor_and_cursor(self, sct: mss.base.MSSBase) -> Tuple[dict, Point]:
        """
        Returns the mss monitor dict that contains the cursor and the current
        cursor position.  Falls back to monitors[1] if detection fails.
        """
        cursor = _get_cursor_pos()
        monitor = self._find_monitor_for_cursor(sct, cursor)
        return monitor, cursor

    def compute_clamped_bbox(
        self,
        cursor: Point,
        monitor: dict,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> Rect:
        """
        Computes a fovea bounding box centred on *cursor* and clamped inside
        *monitor* boundaries.

        Parameters
        ----------
        cursor:  Absolute screen coordinates of the cursor.
        monitor: mss monitor dict with keys left, top, width, height.
        width:   Override fovea width (defaults to self.fovea_width).
        height:  Override fovea height (defaults to self.fovea_height).

        Returns
        -------
        Rect with absolute screen coordinates, guaranteed to fit within monitor.
        """
        w = width if width is not None else self.fovea_width
        h = height if height is not None else self.fovea_height

        mon_left: int = int(monitor.get("left", 0))
        mon_top: int = int(monitor.get("top", 0))
        mon_right: int = mon_left + int(monitor.get("width", w))
        mon_bottom: int = mon_top + int(monitor.get("height", h))

        # Ideal unclamped box centred on cursor
        x1_ideal = cursor.x - w // 2
        y1_ideal = cursor.y - h // 2

        # Clamp so the box never bleeds outside the monitor
        x1 = max(mon_left, min(x1_ideal, mon_right - w))
        y1 = max(mon_top, min(y1_ideal, mon_bottom - h))

        # If the monitor is smaller than the fovea, start at monitor origin
        x1 = max(mon_left, x1)
        y1 = max(mon_top, y1)

        actual_w = min(w, mon_right - x1)
        actual_h = min(h, mon_bottom - y1)

        return Rect(left=x1, top=y1, width=actual_w, height=actual_h)

    def capture_fovea_frame(
        self,
        sct: mss.base.MSSBase,
        jpeg_quality: int = 50,
    ) -> bytes:
        """
        Captures the fovea region, draws the gaze reticle, and returns JPEG bytes.

        The bounding box is only recomputed when the cursor has moved beyond
        *move_threshold* pixels since the last capture (debounce).

        Parameters
        ----------
        sct:          Open mss.MSS() context (caller owns the context).
        jpeg_quality: JPEG quality 1–95 (default 50).

        Returns
        -------
        Raw JPEG bytes with gaze reticle burned in.
        """
        monitor, cursor = self.get_active_monitor_and_cursor(sct)

        # Debounce: only recompute bbox when cursor moved beyond threshold
        if self._last_cursor is None or self._cursor_moved(cursor):
            bbox = self.compute_clamped_bbox(cursor, monitor)
            self._last_bbox = bbox
            self._last_monitor = monitor
            self._last_cursor = cursor
            # Relative cursor position inside the crop
            rel_x = cursor.x - bbox.left
            rel_y = cursor.y - bbox.top
            self._last_rel_cursor = Point(
                max(0, min(rel_x, bbox.width - 1)),
                max(0, min(rel_y, bbox.height - 1)),
            )
        else:
            bbox = self._last_bbox  # type: ignore[assignment]

        # Grab the region
        grab_rect = {
            "left": bbox.left,
            "top": bbox.top,
            "width": bbox.width,
            "height": bbox.height,
        }

        try:
            sct_img = sct.grab(grab_rect)
        except Exception as exc:
            logger.debug(f"[FoveaCapture] sct.grab failed: {exc}")
            # Return a 1×1 blank JPEG so callers never get None
            blank = Image.new("RGB", (1, 1), (0, 0, 0))
            buf = io.BytesIO()
            blank.save(buf, format="JPEG", quality=jpeg_quality)
            return buf.getvalue()

        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        # Draw gaze reticle
        img = self._draw_reticle(img, self._last_rel_cursor or Point(img.width // 2, img.height // 2))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg_quality)
        return buf.getvalue()

    def capture_macro_frame(
        self,
        sct: mss.base.MSSBase,
        jpeg_quality: int = 50,
        all_displays: bool = False,
    ) -> bytes:
        """
        Captures the macro context shot.
        By default, crops ONLY the physical monitor containing the cursor,
        preventing Gemini from seeing all displays simultaneously unless
        explicitly requested via `all_displays=True`.

        Parameters
        ----------
        sct:           Open mss.MSS() context.
        jpeg_quality:  JPEG quality 1–95 (default 50).
        all_displays:  If True, captures the entire unified virtual desktop (sct.monitors[0]).
                       Default False (crops active monitor containing cursor).

        Returns
        -------
        Raw JPEG bytes for the macro context shot.
        """
        if all_displays:
            target = sct.monitors[0] if sct.monitors else {"left": 0, "top": 0, "width": 1920, "height": 1080}
        else:
            monitor, _ = self.get_active_monitor_and_cursor(sct)
            target = {
                "left": int(monitor.get("left", 0)),
                "top": int(monitor.get("top", 0)),
                "width": int(monitor.get("width", 1920)),
                "height": int(monitor.get("height", 1080)),
            }

        try:
            sct_img = sct.grab(target)
        except Exception as exc:
            logger.debug(f"[FoveaCapture] sct.grab failed for macro frame: {exc}")
            blank = Image.new("RGB", (1, 1), (0, 0, 0))
            buf = io.BytesIO()
            blank.save(buf, format="JPEG", quality=jpeg_quality)
            return buf.getvalue()

        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg_quality)
        return buf.getvalue()

    capture_macro_context = capture_macro_frame
    capture_macro = capture_macro_frame

    # ── Private helpers ───────────────────────────────────────────────────────

    def _cursor_moved(self, cursor: Point) -> bool:
        """Returns True if cursor has moved beyond the debounce threshold."""
        if self._last_cursor is None:
            return True
        dx = cursor.x - self._last_cursor.x
        dy = cursor.y - self._last_cursor.y
        return math.hypot(dx, dy) >= self.move_threshold

    def _find_monitor_for_cursor(self, sct: mss.base.MSSBase, cursor: Point) -> dict:
        """
        Identifies which mss physical monitor (index 1+) contains the cursor.
        Falls back to monitors[1] if none match.
        """
        # sct.monitors[0] is the virtual bounding box — skip it
        for mon in sct.monitors[1:]:
            m_left = int(mon.get("left", 0))
            m_top = int(mon.get("top", 0))
            m_right = m_left + int(mon.get("width", 0))
            m_bottom = m_top + int(mon.get("height", 0))
            if m_left <= cursor.x < m_right and m_top <= cursor.y < m_bottom:
                return mon
        # Fallback: primary monitor
        return sct.monitors[1] if len(sct.monitors) > 1 else {"left": 0, "top": 0, "width": 1920, "height": 1080}

    def _draw_reticle(self, img: Image.Image, rel_cursor: Point) -> Image.Image:
        """
        Burns a semi-transparent red gaze reticle at *rel_cursor* into *img*.

        Uses an RGBA overlay merged via alpha_composite so existing pixels
        show through the translucent fill.
        """
        # Create transparent overlay same size as img
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        r = self.reticle_radius
        cx, cy = rel_cursor.x, rel_cursor.y

        # Filled semi-transparent circle
        draw.ellipse(
            [(cx - r, cy - r), (cx + r, cy + r)],
            fill=self.reticle_color,
            outline=(255, 255, 255, 200),
            width=1,
        )

        # Small solid centre dot for precise targeting
        dot_r = max(2, r // 4)
        draw.ellipse(
            [(cx - dot_r, cy - dot_r), (cx + dot_r, cy + dot_r)],
            fill=(255, 60, 60, 230),
        )

        # Merge onto RGB base
        base_rgba = img.convert("RGBA")
        composited = Image.alpha_composite(base_rgba, overlay)
        return composited.convert("RGB")
