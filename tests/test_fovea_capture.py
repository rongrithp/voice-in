"""
tests/test_fovea_capture.py
===========================

Unit tests for FoveaCapturePipeline multi-monitor coordinate synchronization
and macro context shot isolation.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

from src.zero_ui.fovea_capture import FoveaCapturePipeline, Point, Rect
from tests.test_fovea_vision import (
    TestComputeClampedBbox,
    TestGazeReticle,
    TestDebounce,
    TestFindMonitorForCursor,
    _make_monitor,
)


def _make_mock_sct(physical_monitors: list[dict], virtual_bbox: dict = None) -> MagicMock:
    """
    Creates an mss mock where index 0 is the unified multi-display virtual canvas,
    and indices 1+ are the individual physical displays.
    """
    if virtual_bbox is None:
        virtual_bbox = {"left": -1920, "top": 0, "width": 7280, "height": 1440}
    sct = MagicMock()
    sct.monitors = [virtual_bbox] + physical_monitors

    def _grab(rect):
        w = max(1, rect.get("width", 100))
        h = max(1, rect.get("height", 100))
        mock_img = MagicMock()
        mock_img.size = (w, h)
        mock_img.bgra = b"\x00\x00\x00\xff" * (w * h)
        return mock_img

    sct.grab.side_effect = _grab
    return sct


class TestFoveaMacroContextCapture(unittest.TestCase):
    """Tests that macro context shot crops strictly the cursor's monitor, not all displays."""

    def setUp(self):
        self.pipeline = FoveaCapturePipeline(fovea_width=1280, fovea_height=720)
        self.mon_neg = _make_monitor(-1920, 0, 1920, 1080)
        self.mon_pri = _make_monitor(0, 0, 3440, 1440)
        self.mon_right = _make_monitor(3440, 0, 1920, 1080)
        self.virtual_canvas = _make_monitor(-1920, 0, 7280, 1440)
        self.sct = _make_mock_sct([self.mon_neg, self.mon_pri, self.mon_right], self.virtual_canvas)

    def test_macro_shot_on_primary_monitor(self):
        """When cursor is on primary display, macro shot captures only that display."""
        cursor_on_pri = Point(1000, 500)
        with patch("src.zero_ui.fovea_capture._get_cursor_pos", return_value=cursor_on_pri):
            jpeg_bytes = self.pipeline.capture_macro_frame(self.sct)

        self.assertIsInstance(jpeg_bytes, bytes)
        self.assertGreater(len(jpeg_bytes), 0)
        self.sct.grab.assert_called_once_with({
            "left": 0,
            "top": 0,
            "width": 3440,
            "height": 1440,
        })

    def test_macro_shot_on_negative_offset_monitor(self):
        """When cursor is on secondary display to the left (negative X), macro shot isolates it."""
        cursor_on_neg = Point(-500, 300)
        with patch("src.zero_ui.fovea_capture._get_cursor_pos", return_value=cursor_on_neg):
            jpeg_bytes = self.pipeline.capture_macro_frame(self.sct)

        self.assertIsInstance(jpeg_bytes, bytes)
        self.assertGreater(len(jpeg_bytes), 0)
        self.sct.grab.assert_called_once_with({
            "left": -1920,
            "top": 0,
            "width": 1920,
            "height": 1080,
        })

    def test_macro_shot_on_right_monitor(self):
        """When cursor is on right-hand secondary display, macro shot isolates it."""
        cursor_on_right = Point(4000, 200)
        with patch("src.zero_ui.fovea_capture._get_cursor_pos", return_value=cursor_on_right):
            jpeg_bytes = self.pipeline.capture_macro_frame(self.sct)

        self.assertIsInstance(jpeg_bytes, bytes)
        self.assertGreater(len(jpeg_bytes), 0)
        self.sct.grab.assert_called_once_with({
            "left": 3440,
            "top": 0,
            "width": 1920,
            "height": 1080,
        })

    def test_macro_shot_explicit_all_displays(self):
        """When all_displays=True is explicitly requested, macro shot captures virtual canvas."""
        cursor_on_pri = Point(1000, 500)
        with patch("src.zero_ui.fovea_capture._get_cursor_pos", return_value=cursor_on_pri):
            jpeg_bytes = self.pipeline.capture_macro_frame(self.sct, all_displays=True)

        self.assertIsInstance(jpeg_bytes, bytes)
        self.assertGreater(len(jpeg_bytes), 0)
        self.sct.grab.assert_called_once_with(self.virtual_canvas)


if __name__ == "__main__":
    unittest.main()
