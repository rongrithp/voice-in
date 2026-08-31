import pytest
from unittest.mock import MagicMock, patch
from src.hud_overlay import HUDOverlay, HUDState, STATE_CONFIGS

def test_hud_overlay_init():
    hud = HUDOverlay(position="top-center")
    assert hud.position == "top-center"
    assert hud.state == HUDState.IDLE
    assert hud._is_running is False
    assert hud._is_visible is False

def test_hud_overlay_states_and_transitions():
    hud = HUDOverlay()
    mock_root = MagicMock()
    hud.root = mock_root
    hud._is_running = True
    hud._pill_frame = MagicMock()
    hud._label = MagicMock()

    # 1. Test STT Active State
    hud.show_stt()
    assert hud.state == HUDState.STT_ACTIVE
    hud._apply_state(HUDState.STT_ACTIVE)
    hud._label.configure.assert_called_with(
        text=STATE_CONFIGS[HUDState.STT_ACTIVE]["text"],
        bg=STATE_CONFIGS[HUDState.STT_ACTIVE]["bg"],
        fg=STATE_CONFIGS[HUDState.STT_ACTIVE]["fg"]
    )
    mock_root.deiconify.assert_called()

    # 2. Test Live Gemini Active State
    hud.show_live()
    assert hud.state == HUDState.LIVE_ACTIVE
    hud._apply_state(HUDState.LIVE_ACTIVE)
    hud._label.configure.assert_called_with(
        text=STATE_CONFIGS[HUDState.LIVE_ACTIVE]["text"],
        bg=STATE_CONFIGS[HUDState.LIVE_ACTIVE]["bg"],
        fg=STATE_CONFIGS[HUDState.LIVE_ACTIVE]["fg"]
    )

    # 3. Test TTS Active State
    hud.show_tts()
    assert hud.state == HUDState.TTS_ACTIVE
    hud._apply_state(HUDState.TTS_ACTIVE)
    hud._label.configure.assert_called_with(
        text=STATE_CONFIGS[HUDState.TTS_ACTIVE]["text"],
        bg=STATE_CONFIGS[HUDState.TTS_ACTIVE]["bg"],
        fg=STATE_CONFIGS[HUDState.TTS_ACTIVE]["fg"]
    )

    # 4. Test Hide / IDLE State
    hud._is_visible = True
    hud.hide()
    assert hud.state == HUDState.IDLE
    hud._apply_state(HUDState.IDLE)
    mock_root.withdraw.assert_called()
    assert hud._is_visible is False

def test_hud_overlay_reposition():
    hud = HUDOverlay(position="top-center")
    mock_root = MagicMock()
    mock_root.winfo_screenwidth.return_value = 1920
    mock_root.winfo_reqwidth.return_value = 250
    mock_root.winfo_reqheight.return_value = 35
    hud.root = mock_root

    hud._reposition()
    mock_root.geometry.assert_called()

    # Test top-right position
    hud.position = "top-right"
    hud._reposition()
    mock_root.geometry.assert_called()

def test_hud_overlay_stop():
    hud = HUDOverlay()
    mock_root = MagicMock()
    hud.root = mock_root
    hud._is_running = True

    hud.stop()
    assert hud._is_running is False
    assert hud.root is None

def test_hud_overlay_audio_level():
    hud = HUDOverlay()
    mock_root = MagicMock()
    hud.root = mock_root
    hud._is_running = True
    hud._is_visible = True
    hud.state = HUDState.STT_ACTIVE
    hud._label = MagicMock()

    hud.update_audio_level(2500.0)
    mock_root.after.assert_called()

    # Direct apply level
    hud._apply_audio_level("▰▰▰▱")
    call_text = hud._label.configure.call_args.kwargs.get("text", "")
    assert "🔴 [LIVE UPLOADING] STT ACTIVE • MIC ON" in call_text
    assert "▰▰▰▱" in call_text

