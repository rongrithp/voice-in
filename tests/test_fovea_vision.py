"""
tests/test_fovea_vision.py
==========================

Unit tests for the Mouse Fovea Vision pipeline (FoveaCapturePipeline).

All tests are fully offline — mss, ctypes, and PIL are patched so no physical
display or cursor hardware is required.

Coverage
--------
* Bounding box clamping (centre, top-left corner, bottom-right corner)
* Dual-monitor negative X coordinate handling
* Gaze reticle pixel presence in the crop buffer
* Debounce: no recompute within threshold, recompute beyond threshold
"""

from __future__ import annotations

import io
import types as _builtin_types
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from PIL import Image

# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_monitor(left: int, top: int, width: int, height: int) -> dict:
    return {"left": left, "top": top, "width": width, "height": height}


def _make_sct(monitors: list[dict]) -> MagicMock:
    """
    Returns a MagicMock that simulates an mss context with the given monitors
    list (index 0 = virtual bounding box, 1+ = physical monitors).
    """
    sct = MagicMock()
    # monitors[0] is virtual bbox; we prepend a dummy for index 0
    sct.monitors = [_make_monitor(0, 0, 3440, 1440)] + monitors

    def _grab(rect):
        """Returns a synthetic mss screenshot object filled with white."""
        w = rect.get("width", 100)
        h = rect.get("height", 100)
        img = Image.new("RGB", (w, h), (255, 255, 255))
        # mss screenshot objects expose .size and .bgra
        mock_img = MagicMock()
        mock_img.size = (w, h)
        # Build BGRA bytes for a white image
        bgra_data = b"\xff\xff\xff\xff" * (w * h)  # B G R A all 255
        mock_img.bgra = bgra_data
        return mock_img

    sct.grab.side_effect = _grab
    return sct


# ---------------------------------------------------------------------------
# Import target (after helpers, so we can patch at test time)
# ---------------------------------------------------------------------------

from src.zero_ui.fovea_capture import FoveaCapturePipeline, Point, Rect


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestComputeClampedBbox(unittest.TestCase):
    """Tests for FoveaCapturePipeline.compute_clamped_bbox."""

    def setUp(self):
        self.pipeline = FoveaCapturePipeline(
            fovea_width=1280, fovea_height=720, move_threshold=20
        )

    # -- Centre of monitor ----------------------------------------------------

    def test_bbox_center_within_monitor(self):
        """Cursor at monitor centre → bbox is perfectly centred."""
        mon = _make_monitor(left=0, top=0, width=1920, height=1080)
        cursor = Point(960, 540)  # exact centre
        bbox = self.pipeline.compute_clamped_bbox(cursor, mon)

        self.assertEqual(bbox.left, 960 - 1280 // 2)          # 320
        self.assertEqual(bbox.top, 540 - 720 // 2)            # 180
        self.assertEqual(bbox.width, 1280)
        self.assertEqual(bbox.height, 720)

    # -- Top-left corner clamp ------------------------------------------------

    def test_bbox_clamp_top_left_corner(self):
        """Cursor at (0, 0) → bbox clamped so x1=mon.left, y1=mon.top."""
        mon = _make_monitor(left=0, top=0, width=1920, height=1080)
        cursor = Point(0, 0)
        bbox = self.pipeline.compute_clamped_bbox(cursor, mon)

        self.assertEqual(bbox.left, 0, "Left edge must clamp to monitor left")
        self.assertEqual(bbox.top, 0, "Top edge must clamp to monitor top")
        self.assertEqual(bbox.width, 1280)
        self.assertEqual(bbox.height, 720)

    # -- Bottom-right corner clamp --------------------------------------------

    def test_bbox_clamp_bottom_right_corner(self):
        """Cursor at monitor bottom-right → bbox clamped so x2 == mon.right, y2 == mon.bottom."""
        mon = _make_monitor(left=0, top=0, width=1920, height=1080)
        cursor = Point(1919, 1079)
        bbox = self.pipeline.compute_clamped_bbox(cursor, mon)

        self.assertEqual(bbox.right, 1920, "Right edge must not exceed monitor right")
        self.assertEqual(bbox.bottom, 1080, "Bottom edge must not exceed monitor bottom")
        self.assertEqual(bbox.width, 1280)
        self.assertEqual(bbox.height, 720)

    # -- Dual-monitor with negative left offset --------------------------------

    def test_bbox_dual_monitor_negative_x(self):
        """
        Cursor on a monitor with negative left offset (secondary monitor to the left).
        Verifies bbox is correctly clamped within that monitor's coordinate space.
        """
        mon = _make_monitor(left=-1920, top=0, width=1920, height=1080)
        cursor = Point(-10, 540)  # near left edge of secondary monitor
        bbox = self.pipeline.compute_clamped_bbox(cursor, mon)

        # bbox must not extend beyond monitor's left edge
        self.assertGreaterEqual(bbox.left, -1920, "Left must not bleed outside secondary monitor")
        # bbox must not extend beyond monitor's right edge (left + width = 0)
        self.assertLessEqual(bbox.right, 0, "Right must not bleed into primary monitor space")
        self.assertEqual(bbox.width, 1280)
        self.assertEqual(bbox.height, 720)

    # -- Monitor smaller than fovea -------------------------------------------

    def test_bbox_monitor_smaller_than_fovea(self):
        """If monitor is narrower than fovea, bbox is clamped to monitor size."""
        mon = _make_monitor(left=0, top=0, width=800, height=600)
        cursor = Point(400, 300)
        bbox = self.pipeline.compute_clamped_bbox(cursor, mon)

        # Width/height should never exceed monitor dimensions
        self.assertLessEqual(bbox.width, 800)
        self.assertLessEqual(bbox.height, 600)
        self.assertGreaterEqual(bbox.left, 0)
        self.assertGreaterEqual(bbox.top, 0)


# ---------------------------------------------------------------------------

class TestGazeReticle(unittest.TestCase):
    """Tests that the gaze reticle renders at the correct relative position."""

    def _make_pipeline_with_cursor(self, cursor_x: int, cursor_y: int, mon: dict):
        """
        Sets up a pipeline with a pre-seeded _last_cursor/_last_bbox/_last_rel_cursor
        so capture_fovea_frame() does not need to call GetCursorPos / sct.grab.
        """
        pipeline = FoveaCapturePipeline(
            fovea_width=1280, fovea_height=720, move_threshold=20
        )
        bbox = pipeline.compute_clamped_bbox(Point(cursor_x, cursor_y), mon)
        pipeline._last_cursor = Point(cursor_x, cursor_y)
        pipeline._last_bbox = bbox
        pipeline._last_monitor = mon
        rel_x = cursor_x - bbox.left
        rel_y = cursor_y - bbox.top
        pipeline._last_rel_cursor = Point(
            max(0, min(rel_x, bbox.width - 1)),
            max(0, min(rel_y, bbox.height - 1)),
        )
        return pipeline, bbox

    def test_reticle_rendered_at_relative_cursor(self):
        """
        After capture_fovea_frame() on a synthetic white image, red pixels must
        exist near the expected relative cursor position inside the crop.
        """
        mon = _make_monitor(left=0, top=0, width=1920, height=1080)
        cursor_x, cursor_y = 960, 540
        pipeline, bbox = self._make_pipeline_with_cursor(cursor_x, cursor_y, mon)

        sct = _make_sct([mon])

        # Patch GetCursorPos so _last_cursor is not refreshed (debounce won't trigger
        # since we pre-seed _last_cursor at the same position)
        with patch("src.zero_ui.fovea_capture._get_cursor_pos", return_value=Point(cursor_x, cursor_y)):
            jpeg_bytes = pipeline.capture_fovea_frame(sct, jpeg_quality=85)

        self.assertIsInstance(jpeg_bytes, bytes)
        self.assertGreater(len(jpeg_bytes), 0)

        # Decode and inspect pixels around the expected reticle centre
        img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
        rel_cx = pipeline._last_rel_cursor.x
        rel_cy = pipeline._last_rel_cursor.y

        # Sample pixels in a small radius around the reticle centre
        r = pipeline.reticle_radius
        found_red = False
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                px = rel_cx + dx
                py = rel_cy + dy
                if 0 <= px < img.width and 0 <= py < img.height:
                    pixel = img.getpixel((px, py))
                    # Red channel significantly dominant (reticle is red on white background)
                    if pixel[0] > 150 and pixel[1] < 150 and pixel[2] < 150:
                        found_red = True
                        break
            if found_red:
                break

        self.assertTrue(
            found_red,
            f"Expected red reticle pixels near ({rel_cx}, {rel_cy}) but none found. "
            f"Image size: {img.size}",
        )


# ---------------------------------------------------------------------------

class TestDebounce(unittest.TestCase):
    """Tests that the debounce threshold is correctly applied."""

    def setUp(self):
        self.pipeline = FoveaCapturePipeline(
            fovea_width=1280, fovea_height=720, move_threshold=20
        )
        self.mon = _make_monitor(left=0, top=0, width=1920, height=1080)

    def test_no_recompute_within_threshold(self):
        """Cursor moves 10 px (< 20 threshold) → _cursor_moved returns False."""
        self.pipeline._last_cursor = Point(500, 500)
        # Move by 7 px diagonally ≈ 9.9 px, well within 20-px threshold
        new_cursor = Point(507, 507)
        self.assertFalse(self.pipeline._cursor_moved(new_cursor))

    def test_recompute_beyond_threshold(self):
        """Cursor moves 25 px (> 20 threshold) → _cursor_moved returns True."""
        self.pipeline._last_cursor = Point(500, 500)
        # Move 25 px along X axis
        new_cursor = Point(525, 500)
        self.assertTrue(self.pipeline._cursor_moved(new_cursor))

    def test_no_last_cursor_always_moves(self):
        """When _last_cursor is None, _cursor_moved always returns True."""
        self.pipeline._last_cursor = None
        self.assertTrue(self.pipeline._cursor_moved(Point(0, 0)))

    def test_exact_threshold_is_recomputed(self):
        """A displacement of exactly move_threshold pixels triggers recompute."""
        self.pipeline._last_cursor = Point(0, 0)
        new_cursor = Point(20, 0)  # exactly 20 px
        self.assertTrue(self.pipeline._cursor_moved(new_cursor))


# ---------------------------------------------------------------------------

class TestFindMonitorForCursor(unittest.TestCase):
    """Tests for _find_monitor_for_cursor with multi-monitor layouts."""

    def setUp(self):
        self.pipeline = FoveaCapturePipeline()

    def _sct_from_physical(self, physical: list[dict]) -> MagicMock:
        sct = MagicMock()
        sct.monitors = [_make_monitor(0, 0, 5360, 1440)] + physical
        return sct

    def test_cursor_on_primary(self):
        monitors = [
            _make_monitor(0, 0, 3440, 1440),
            _make_monitor(3440, 0, 1920, 1080),
        ]
        sct = self._sct_from_physical(monitors)
        mon = self.pipeline._find_monitor_for_cursor(sct, Point(1000, 500))
        self.assertEqual(mon["left"], 0)
        self.assertEqual(mon["width"], 3440)

    def test_cursor_on_secondary(self):
        monitors = [
            _make_monitor(0, 0, 3440, 1440),
            _make_monitor(3440, 0, 1920, 1080),
        ]
        sct = self._sct_from_physical(monitors)
        mon = self.pipeline._find_monitor_for_cursor(sct, Point(4000, 200))
        self.assertEqual(mon["left"], 3440)

    def test_cursor_on_negative_x_monitor(self):
        monitors = [
            _make_monitor(-1920, 0, 1920, 1080),
            _make_monitor(0, 0, 3440, 1440),
        ]
        sct = self._sct_from_physical(monitors)
        mon = self.pipeline._find_monitor_for_cursor(sct, Point(-500, 300))
        self.assertEqual(mon["left"], -1920)


if __name__ == "__main__":
    unittest.main()
