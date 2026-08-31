import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
from src.screen_capture import copy_image_to_clipboard, capture_monitor_to_clipboard

def test_copy_image_to_clipboard_success():
    img = Image.new("RGB", (2, 2), color=(255, 0, 0))
    with patch("win32clipboard.OpenClipboard"), \
         patch("win32clipboard.EmptyClipboard"), \
         patch("win32clipboard.SetClipboardData") as mock_set, \
         patch("win32clipboard.CloseClipboard"):
        result = copy_image_to_clipboard(img)
        assert result is True
        mock_set.assert_called_once()

def test_capture_monitor_to_clipboard_valid_monitor():
    mock_sct = MagicMock()
    mock_sct.monitors = [
        {"top": 0, "left": 0, "width": 3840, "height": 1080}, # all
        {"top": 0, "left": 0, "width": 1920, "height": 1080}, # mon 1
        {"top": 0, "left": 1920, "width": 1920, "height": 1080}, # mon 2
    ]
    mock_img = MagicMock()
    mock_img.size = (1920, 1080)
    mock_img.bgra = b"\x00" * (1920 * 1080 * 4)
    mock_sct.grab.return_value = mock_img
    mock_sct.__enter__.return_value = mock_sct

    with patch("mss.MSS", return_value=mock_sct), \
         patch("src.screen_capture.copy_image_to_clipboard", return_value=True) as mock_copy:
        res = capture_monitor_to_clipboard(1)
        assert res is True
        mock_copy.assert_called_once()

def test_capture_monitor_to_clipboard_out_of_range():
    mock_sct = MagicMock()
    mock_sct.monitors = [
        {"top": 0, "left": 0, "width": 1920, "height": 1080},
        {"top": 0, "left": 0, "width": 1920, "height": 1080},
    ]
    mock_sct.__enter__.return_value = mock_sct

    with patch("mss.MSS", return_value=mock_sct):
        res = capture_monitor_to_clipboard(5) # Monitor 5 does not exist
        assert res is False


def test_log_detected_monitors():
    from src.screen_capture import log_detected_monitors
    mock_sct = MagicMock()
    mock_sct.monitors = [
        {"top": 0, "left": 0, "width": 3840, "height": 1080},
        {"top": 0, "left": 0, "width": 1920, "height": 1080},
        {"top": 0, "left": 1920, "width": 1920, "height": 1080},
    ]
    mock_sct.__enter__.return_value = mock_sct

    with patch("mss.MSS", return_value=mock_sct):
        # Should execute cleanly without error
        log_detected_monitors()


def test_get_physical_monitors():
    from src.screen_capture import get_physical_monitors
    mock_sct = MagicMock()
    mock_sct.monitors = [
        {"top": 0, "left": 0, "width": 5760, "height": 1080}, # all
        {"top": 0, "left": 0, "width": 1920, "height": 1080}, # mon 1
        {"top": 0, "left": 1920, "width": 1920, "height": 1080}, # mon 2
        {"top": 0, "left": 3840, "width": 1920, "height": 1080}, # mon 3
    ]
    mock_sct.__enter__.return_value = mock_sct

    with patch("mss.MSS", return_value=mock_sct):
        mons = get_physical_monitors()
        assert len(mons) == 3
        assert mons[0]["index"] == 1
        assert "Primary" in mons[0]["name"]
        assert mons[1]["index"] == 2
        assert mons[2]["index"] == 3


def test_grab_monitor_thumbnail():
    from src.screen_capture import grab_monitor_thumbnail
    mock_sct = MagicMock()
    mock_sct.monitors = [
        {"top": 0, "left": 0, "width": 3840, "height": 1080},
        {"top": 0, "left": 0, "width": 1920, "height": 1080},
    ]
    mock_img = MagicMock()
    mock_img.size = (1920, 1080)
    mock_img.bgra = b"\x00" * (1920 * 1080 * 4)
    mock_sct.grab.return_value = mock_img
    mock_sct.__enter__.return_value = mock_sct

    with patch("mss.MSS", return_value=mock_sct):
        thumb = grab_monitor_thumbnail(1, max_width=180, max_height=100)
        assert thumb is not None
        assert thumb.width <= 180
        assert thumb.height <= 100

        # Out of bounds monitor
        thumb_none = grab_monitor_thumbnail(99)
        assert thumb_none is None


def test_ultrawide_remapped_as_monitor_1():
    """Verify that UltraWide display (3440x1440 at 0,0) is mapped to Monitor 1 even if enumerated 2nd/3rd."""
    from src.screen_capture import get_ordered_physical_monitors, get_monitor_dict, get_physical_monitors
    mock_sct = MagicMock()
    # Simulate multi-monitor setup where side vertical monitor is index 1 and UltraWide is index 2
    mock_sct.monitors = [
        {"left": 0, "top": -280, "width": 4520, "height": 2800},
        {"left": 3440, "top": -280, "width": 1080, "height": 1920, "is_primary": False}, # Side monitor
        {"left": 0, "top": 0, "width": 3440, "height": 1440, "is_primary": True},        # UltraWide Master Display
        {"left": 985, "top": 1440, "width": 1920, "height": 1080, "is_primary": False}   # Bottom monitor
    ]
    mock_sct.__enter__.return_value = mock_sct

    ordered = get_ordered_physical_monitors(mock_sct)
    assert len(ordered) == 3
    # Monitor 1 MUST be the UltraWide Master Display (3440x1440 at 0,0)
    assert ordered[0]["width"] == 3440
    assert ordered[0]["height"] == 1440
    assert ordered[0]["left"] == 0
    assert ordered[0]["top"] == 0

    mon1_dict = get_monitor_dict(1, mock_sct)
    assert mon1_dict["width"] == 3440
    assert mon1_dict["height"] == 1440

    with patch("mss.MSS", return_value=mock_sct):
        phys = get_physical_monitors()
        assert phys[0]["index"] == 1
        assert "Primary UltraWide" in phys[0]["name"]
        assert phys[0]["width"] == 3440

