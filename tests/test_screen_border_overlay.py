import pytest
from unittest.mock import MagicMock, patch
from src.screen_border_overlay import ScreenBorderOverlay

def test_screen_border_overlay_init():
    border = ScreenBorderOverlay(border_width=3, border_color="#00ff66")
    assert border.border_width == 3
    assert border.border_color == "#00ff66"
    assert border._is_running is False
    assert border._is_visible is False

def test_screen_border_overlay_show_hide():
    border = ScreenBorderOverlay()
    mock_root = MagicMock()
    mock_canvas = MagicMock()
    border.root = mock_root
    border._canvas = mock_canvas
    border._is_running = True

    # Show
    border._apply_show()
    mock_root.deiconify.assert_called()
    assert border._is_visible is True

    # Hide
    border._apply_hide()
    mock_root.withdraw.assert_called()
    assert border._is_visible is False

def test_screen_border_overlay_reposition_and_draw():
    border = ScreenBorderOverlay()
    mock_root = MagicMock()
    mock_canvas = MagicMock()
    border.root = mock_root
    border._canvas = mock_canvas

    border._reposition_and_draw()
    mock_root.geometry.assert_called()
    mock_canvas.create_rectangle.assert_called()

def test_screen_border_overlay_stop():
    border = ScreenBorderOverlay()
    mock_root = MagicMock()
    border.root = mock_root
    border._is_running = True

    border.stop()
    assert border._is_running is False
    assert border.root is None
