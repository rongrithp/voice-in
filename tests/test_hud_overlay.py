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

    # 1. Test STT Connecting & Active & Finalizing States
    hud.show_stt_connecting()
    assert hud.state == HUDState.STT_CONNECTING
    hud._apply_state(HUDState.STT_CONNECTING)
    assert "CONNECTING" in hud._label.configure.call_args.kwargs["text"]

    hud.show_stt()
    assert hud.state == HUDState.STT_ACTIVE
    hud._apply_state(HUDState.STT_ACTIVE)
    assert "🔴 RECORDING • SPEAK NOW" in hud._label.configure.call_args.kwargs["text"]
    mock_root.deiconify.assert_called()

    hud.show_stt_finalizing()
    assert hud.state == HUDState.STT_FINALIZING
    hud._apply_state(HUDState.STT_FINALIZING)
    assert "FINALIZING" in hud._label.configure.call_args.kwargs["text"]

    # 2. Test Live Gemini Connecting, Handshake, Active, Error, Closing States
    hud.show_live_connecting()
    assert hud.state == HUDState.LIVE_CONNECTING
    hud._apply_state(HUDState.LIVE_CONNECTING)
    assert "[1/2] CONNECTING" in hud._label.configure.call_args.kwargs["text"]

    hud.show_live_handshake()
    assert hud.state == HUDState.LIVE_HANDSHAKE
    hud._apply_state(HUDState.LIVE_HANDSHAKE)
    assert "[2/2] INITIALIZING" in hud._label.configure.call_args.kwargs["text"]

    hud.show_live()
    assert hud.state == HUDState.LIVE_ACTIVE
    hud._apply_state(HUDState.LIVE_ACTIVE)
    assert "🟢 READY • SPEAK NOW" in hud._label.configure.call_args.kwargs["text"]

    hud.show_live_error()
    assert hud.state == HUDState.LIVE_ERROR
    hud._apply_state(HUDState.LIVE_ERROR)
    assert "CONNECTION FAILED" in hud._label.configure.call_args.kwargs["text"]

    hud.show_live_closing()
    assert hud.state == HUDState.LIVE_CLOSING
    hud._apply_state(HUDState.LIVE_CLOSING)
    assert "CLOSING" in hud._label.configure.call_args.kwargs["text"]

    # 3. Test TTS Active State
    hud.show_tts()
    assert hud.state == HUDState.TTS_ACTIVE
    hud._apply_state(HUDState.TTS_ACTIVE)
    assert "🔊 READING" in hud._label.configure.call_args.kwargs["text"]

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
    assert "🔴 RECORDING • SPEAK NOW" in call_text
    assert "▰▰▰▱" in call_text

