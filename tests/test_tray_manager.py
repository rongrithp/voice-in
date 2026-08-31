import pytest
from unittest.mock import patch, MagicMock
from src.tray_manager import TrayManager, DaemonStatus, create_status_image

def test_create_status_image():
    for status in [DaemonStatus.READY, DaemonStatus.ACTIVE, DaemonStatus.ERROR]:
        img = create_status_image(status, size=64)
        assert img is not None
        assert img.size == (64, 64)

def test_tray_manager_lifecycle():
    on_reload = MagicMock()
    on_unmute = MagicMock()
    on_exit = MagicMock()

    tray = TrayManager(
        on_reload=on_reload,
        on_emergency_unmute=on_unmute,
        on_exit=on_exit
    )
    assert tray.status == DaemonStatus.READY

    # Update status
    tray.icon = MagicMock()
    tray.update_status(DaemonStatus.ACTIVE, "Active Recording")
    assert tray.status == DaemonStatus.ACTIVE
    assert tray.icon.title == "Active Recording"

    # Test menu handlers
    tray._handle_reload(None, None)
    tray._handle_emergency_unmute(None, None)
    tray._handle_exit(None, None)
    on_exit.assert_called_once()

def test_tray_manager_speed_menu():
    on_speed = MagicMock()
    tray = TrayManager(
        on_speed_change=on_speed,
        current_speed=1.0
    )
    speed_menu = tray._create_speed_menu()
    assert speed_menu is not None

    # Simulate selecting 1.5x speed
    for item in speed_menu.items:
        if "1.50x" in item.text:
            item._action(None, item)
            break

    assert tray.current_speed == 1.5
    on_speed.assert_called_with(1.5)

def test_tray_manager_voice_menu():
    on_voice = MagicMock()
    tray = TrayManager(
        on_voice_change=on_voice,
        current_voice="th-TH-Neural2-C"
    )
    voice_menu = tray._create_voice_menu()
    assert voice_menu is not None

    # Simulate selecting Standard-A
    for item in voice_menu.items:
        if "Standard-A" in item.text:
            item._action(None, item)
            break

    assert tray.current_voice == "th-TH-Standard-A"
    on_voice.assert_called_with("th-TH-Standard-A")

def test_tray_manager_usage_menu():
    mock_tracker = MagicMock()
    mock_tracker.get_current_month_summary.return_value = {
        "month": "2026-08",
        "stt_min": 2.5,
        "stt_cost_thb": 1.44,
        "tts_chars": 15000,
        "tts_cost_thb": 8.64,
        "total_cost_thb": 10.08
    }
    mock_tracker.storage_path = MagicMock()
    mock_tracker.storage_path.exists.return_value = True
    on_reset = MagicMock()

    tray = TrayManager(
        usage_tracker=mock_tracker,
        on_reset_usage=on_reset
    )
    usage_menu = tray._create_usage_menu()
    assert usage_menu is not None

    # Verify menu text contents
    texts = [item.text for item in usage_menu.items if hasattr(item, "text")]
    assert any("2026-08" in t and "10.08 THB" in t for t in texts)
    assert any("2.5 min" in t for t in texts)
    assert any("15,000 chars" in t for t in texts)
    assert any("Open Usage Stats File" in t for t in texts)

    # Test open file action
    with patch("os.startfile") as mock_startfile:
        for item in usage_menu.items:
            if hasattr(item, "text") and "Open" in item.text:
                item._action(None, item)
                break
        mock_startfile.assert_called_once()

    # Test reset action
    for item in usage_menu.items:
        if hasattr(item, "text") and "Reset" in item.text:
            item._action(None, item)
            break

    on_reset.assert_called_once()


def test_tray_manager_live_toggle():
    on_live_toggle = MagicMock()
    tray = TrayManager(
        on_live_toggle=on_live_toggle,
        is_live_active_callback=lambda: True
    )
    menu = tray._build_menu()
    assert menu is not None

    # Check live co-pilot label
    texts = [item.text for item in menu.items if hasattr(item, "text")]
    assert any("Gemini Live Co-pilot (F20)" in t and "ON" in t for t in texts)

    # Trigger handler
    mock_thread = MagicMock()
    with patch("threading.Thread", return_value=mock_thread):
        tray._handle_live_toggle(None, None)
        mock_thread.start.assert_called_once()


def test_tray_manager_windows_local_tts():
    on_local_tts = MagicMock()
    tray = TrayManager(
        on_windows_local_tts=on_local_tts
    )
    menu = tray._build_menu()
    assert menu is not None

    texts = [item.text for item in menu.items if hasattr(item, "text")]
    assert any("Windows Local TTS (F21)" in t for t in texts)

    mock_thread = MagicMock()
    with patch("threading.Thread", return_value=mock_thread):
        tray._handle_windows_local_tts(None, None)
        mock_thread.start.assert_called_once()


def test_tray_manager_read_down():
    on_read_down = MagicMock()
    tray = TrayManager(
        on_read_down=on_read_down
    )
    menu = tray._build_menu()
    assert menu is not None

    texts = [item.text for item in menu.items if hasattr(item, "text")]
    assert any("TTS Read Down (F16)" in t for t in texts)

    mock_thread = MagicMock()
    with patch("threading.Thread", return_value=mock_thread):
        tray._handle_read_down(None, None)
        mock_thread.start.assert_called_once()


def test_tray_manager_streaming_active_indicator():
    tray = TrayManager()
    tray.icon = MagicMock()

    # Streaming active tooltip update
    active_tooltip = "[🔴 LIVE STREAMING ACTIVE - INGESTING AUDIO (F13 STT)]"
    tray.update_status(DaemonStatus.ACTIVE, active_tooltip)
    assert tray.status == DaemonStatus.ACTIVE
    assert tray.icon.title == active_tooltip
    tray.icon.icon = create_status_image(DaemonStatus.ACTIVE)
    assert tray.icon.icon is not None

    # Idle tooltip update
    ready_tooltip = "Voice Operating Hub: Ready (F13: STT, F20: Live Co-pilot)"
    tray.update_status(DaemonStatus.READY, ready_tooltip)
    assert tray.status == DaemonStatus.READY
    assert tray.icon.title == ready_tooltip


