import time
from unittest.mock import MagicMock, patch
import pytest

from src.robot_hud_widget import RobotHUDWidget, RobotLEDState
from src.live_copilot import LiveCopilotSession


def test_robot_hud_widget_init():
    # Test default compact 56x56 footprint
    widget = RobotHUDWidget()
    assert widget.size == 56
    assert widget.state in (RobotLEDState.INACTIVE, RobotLEDState.OFF)
    assert widget._is_running is False

    # Test custom position
    custom = RobotHUDWidget(size=48, initial_x=120, initial_y=240)
    assert custom.size == 48
    assert custom.initial_x == 120
    assert custom.initial_y == 240


def test_robot_hud_widget_states():
    widget = RobotHUDWidget()
    mock_root = MagicMock()
    mock_canvas = MagicMock()
    widget.root = mock_root
    widget._canvas = mock_canvas
    widget._is_running = True

    # 1. State 3: DISCONNECTED / INACTIVE -> Dim Charcoal #333333
    widget.set_inactive()
    assert widget.state == RobotLEDState.INACTIVE
    assert widget._get_led_color() == "#333333"

    # 2. State 1: IDLE / STANDBY -> Solid Bright Green #00FF00
    widget.set_idle()
    assert widget.state == RobotLEDState.IDLE
    assert widget._get_led_color() == "#00FF00"

    # 3. State 2: ACTIVE DATA STREAMING -> Pulsing/Blinking Green between #00FF00 and #003300
    widget.set_streaming()
    assert widget.state == RobotLEDState.STREAMING
    widget._blink_phase = True
    assert widget._get_led_color() == "#00FF00"
    widget._blink_phase = False
    assert widget._get_led_color() == "#003300"


def test_robot_hud_widget_traffic_state_hook():
    widget = RobotHUDWidget()
    widget.root = MagicMock()
    widget._is_running = True

    # set_traffic_state(True) -> STREAMING
    widget.set_traffic_state(True)
    assert widget.state in (RobotLEDState.STREAMING, RobotLEDState.TRANSMITTING)

    # set_traffic_state(False) -> IDLE
    widget.set_traffic_state(False)
    assert widget.state == RobotLEDState.IDLE

    # set_inactive()
    widget.set_inactive()
    assert widget.state in (RobotLEDState.INACTIVE, RobotLEDState.OFF)

    # set_traffic_state(False) while inactive stays inactive
    widget.set_traffic_state(False)
    assert widget.state in (RobotLEDState.INACTIVE, RobotLEDState.OFF)


def test_robot_hud_widget_drag():
    widget = RobotHUDWidget()
    mock_root = MagicMock()
    mock_root.winfo_x.return_value = 500
    mock_root.winfo_y.return_value = 600
    widget.root = mock_root

    event_start = MagicMock()
    event_start.x = 20
    event_start.y = 20
    widget._on_drag_start(event_start)

    event_motion = MagicMock()
    event_motion.x = 35  # dx = +15
    event_motion.y = 10  # dy = -10
    widget._on_drag_motion(event_motion)

    mock_root.geometry.assert_called_with("+515+590")


def test_live_copilot_traffic_state_data_flow():
    session = LiveCopilotSession()
    traffic_events = []
    session.on_traffic_state = lambda is_tx: traffic_events.append(is_tx)

    session._is_running = True

    # Trigger traffic with quick debounce (50ms)
    session.notify_traffic(debounce_ms=50.0)
    assert session._is_transmitting is True
    assert traffic_events == [True]

    # Repeated calls while transmitting should not re-trigger true
    session.notify_traffic(debounce_ms=50.0)
    assert traffic_events == [True]

    # Wait for debounce to expire
    time.sleep(0.08)
    assert session._is_transmitting is False
    assert traffic_events == [True, False]

    # Stop resets traffic state cleanly
    session.stop()
    assert session._is_transmitting is False
