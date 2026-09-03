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

def test_screen_border_overlay_snap_to_monitor_multi_monitor():
    border = ScreenBorderOverlay()
    mock_root = MagicMock()
    mock_canvas = MagicMock()
    border.root = mock_root
    border._canvas = mock_canvas

    # Primary monitor (3440x1440 at 0, 0)
    border._snap_to_monitor({"left": 0, "top": 0, "width": 3440, "height": 1440})
    mock_root.geometry.assert_called_with("3440x1440+0+0")

    # Secondary monitor with negative coordinates (left of primary: -1920, top: -50)
    border._snap_to_monitor({"left": -1920, "top": -50, "width": 1920, "height": 1080})
    mock_root.geometry.assert_called_with("1920x1080+-1920+-50")

    # Secondary monitor to the right (3440, 100)
    border._snap_to_monitor({"left": 3440, "top": 100, "width": 1920, "height": 1080})
    mock_root.geometry.assert_called_with("1920x1080+3440+100")

def test_screen_border_overlay_stop():
    border = ScreenBorderOverlay()
    mock_root = MagicMock()
    border.root = mock_root
    border._is_running = True

    border.stop()
    assert border._is_running is False
    assert border.root is None


def test_screen_border_overlay_poll_cursor_moves_across_monitors():
    border = ScreenBorderOverlay()
    mock_root = MagicMock()
    mock_canvas = MagicMock()
    border.root = mock_root
    border._canvas = mock_canvas
    border._is_running = True
    border._is_visible = True
    border._current_monitor_key = (0, 0, 3440, 1440)

    secondary_mon = {"left": 3440, "top": 0, "width": 1920, "height": 1080}
    with patch.object(border, "_get_cursor_monitor", return_value=secondary_mon), \
         patch.object(border, "_snap_to_monitor", wraps=border._snap_to_monitor) as spy_snap, \
         patch.object(border, "_force_click_through"):
        border._poll_cursor()

        spy_snap.assert_called_once_with(mon_dict=secondary_mon)
        mock_root.geometry.assert_called_with("1920x1080+3440+0")
        mock_canvas.delete.assert_called_with("all")
        mock_canvas.create_rectangle.assert_called()
        assert border._current_monitor_key == (3440, 0, 1920, 1080)
        mock_root.after.assert_called_with(50, border._poll_cursor)


def test_screen_border_overlay_poll_cursor_same_monitor_does_not_redraw():
    border = ScreenBorderOverlay()
    mock_root = MagicMock()
    mock_canvas = MagicMock()
    border.root = mock_root
    border._canvas = mock_canvas
    border._is_running = True
    border._is_visible = True
    border._current_monitor_key = (0, 0, 3440, 1440)

    primary_mon = {"left": 0, "top": 0, "width": 3440, "height": 1440}
    with patch.object(border, "_get_cursor_monitor", return_value=primary_mon), \
         patch.object(border, "_snap_to_monitor") as spy_snap:
        border._poll_cursor()

        spy_snap.assert_not_called()
        mock_root.after.assert_called_with(50, border._poll_cursor)


def test_screen_border_overlay_poll_cursor_hidden_reschedules():
    border = ScreenBorderOverlay()
    mock_root = MagicMock()
    mock_canvas = MagicMock()
    border.root = mock_root
    border._canvas = mock_canvas
    border._is_running = True
    border._is_visible = False

    with patch.object(border, "_get_cursor_monitor") as mock_mon, \
         patch.object(border, "_snap_to_monitor") as mock_snap:
        border._poll_cursor()

        mock_mon.assert_not_called()
        mock_snap.assert_not_called()
        mock_root.after.assert_called_with(50, border._poll_cursor)
