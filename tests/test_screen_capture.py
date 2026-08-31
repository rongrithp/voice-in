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
