import pytest
from unittest.mock import patch, MagicMock, call
from src.actuator import (
    TextActuator,
    sound_feedback,
    inject_to_cursor,
    paste_text,
    type_text,
    _send_ctrl_v,
    _send_space
)

def test_paste_text_success():
    with patch("pyperclip.copy") as mock_copy, \
         patch("src.actuator._send_ctrl_v") as mock_ctrl_v, \
         patch("src.actuator._send_space") as mock_space, \
         patch("time.sleep"):
        paste_text("สวัสดีเจมินาย")
        mock_copy.assert_called_once_with("สวัสดีเจมินาย")
        mock_ctrl_v.assert_called_once()
        mock_space.assert_called_once()

def test_send_ctrl_v_win32():
    mock_user32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32

    with patch("ctypes.windll", mock_windll, create=True), \
         patch("time.sleep"):
        _send_ctrl_v()
        assert mock_user32.keybd_event.call_count == 4
        calls = [
            call(0x11, 0, 0, 0),
            call(0x56, 0, 0, 0),
            call(0x56, 0, 2, 0),
            call(0x11, 0, 2, 0)
        ]
        mock_user32.keybd_event.assert_has_calls(calls)

def test_send_space_win32():
    mock_user32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32

    with patch("ctypes.windll", mock_windll, create=True), \
         patch("time.sleep"):
        _send_space()
        assert mock_user32.keybd_event.call_count == 2
        calls = [
            call(0x20, 0, 0, 0),
            call(0x20, 0, 2, 0)
        ]
        mock_user32.keybd_event.assert_has_calls(calls)

def test_paste_text_empty():
    with patch("pyperclip.copy") as mock_copy, \
         patch("src.actuator._send_ctrl_v") as mock_ctrl_v:
        paste_text("")
        mock_copy.assert_not_called()
        mock_ctrl_v.assert_not_called()

def test_paste_text_fallback():
    with patch("pyperclip.copy", side_effect=Exception("Clipboard busy")), \
         patch("keyboard.write") as mock_write:
        paste_text("ข้อความสำรอง")
        mock_write.assert_called_once_with("ข้อความสำรอง ")

def test_sound_feedback():
    with patch("winsound.Beep") as mock_beep:
        sound_feedback(880, 50)
        import time
        time.sleep(0.1)
        mock_beep.assert_called_once_with(880, 50)

