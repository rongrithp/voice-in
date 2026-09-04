import time
import numpy as np
import pytest
from unittest.mock import patch, MagicMock
import config
from src.app import VoiceOperatingHubApp
from src.tray_manager import DaemonStatus
from src.audio_player import PlaybackState

@pytest.fixture(autouse=True)
def mock_beeps():
    with patch("winsound.Beep"), \
         patch("google.genai.Client"):
        yield

@pytest.fixture
def app():
    mock_live_session = MagicMock()
    with patch("keyboard.add_hotkey"), \
         patch("winsound.Beep"), \
         patch("src.router.httpx.Client"), \
         patch("src.tray_manager.pystray"), \
         patch.object(VoiceOperatingHubApp, "_warmup_engines"):
        application = VoiceOperatingHubApp(start_gui=False)
        application._warmup_event.set()
        application.play_start_chime = MagicMock()
        application.play_stop_chime = MagicMock()
        application.sound_feedback = MagicMock()
        application.engine.create_live_session = MagicMock(return_value=mock_live_session)
        return application

def test_app_stt_ducking_and_unmute(app):
    with patch("src.audio_control.mute") as mock_mute, \
         patch("src.audio_control.unmute") as mock_unmute, \
         patch.object(app, "audio_worker"), \
         patch.object(app, "stream_capture"):

        assert app.is_streaming is False

        # Toggle ON -> Must call audio_control.mute()
        app.toggle()
        assert app.is_streaming is True
        mock_mute.assert_called_once()

        # Toggle OFF -> Must call audio_control.unmute()
        app.toggle()
        assert app.is_streaming is False
        mock_unmute.assert_called_once()

def test_app_stt_emergency_double_click(app):
    with patch("src.audio_control.mute"), \
         patch("src.audio_control.unmute") as mock_unmute, \
         patch.object(app, "audio_worker"), \
         patch.object(app, "stream_capture"):

        app.toggle() # ON
        assert app.is_streaming is True

        # Double click abort
        app.emergency_flush_stt()
        assert app.is_streaming is False
        mock_unmute.assert_called()

def test_app_tts_read_selected(app):
    import threading
    playback_event = threading.Event()
    def fake_start_queue(audio, is_last=False):
        playback_event.set()
        return True

    with patch("src.app.copy_selected_text", return_value="ข้อความทดสอบ TTS"), \
         patch("src.app.copy_cursor_to_bottom", return_value="ข้อความทดสอบ TTS"), \
         patch.object(app.tts_engine, "synthesize", return_value=b"AUDIO_DATA"), \
         patch.object(app.audio_player, "start_queue_playback", side_effect=fake_start_queue) as mock_start_queue, \
         patch.object(app.audio_player, "stop") as mock_stop, \
         patch("src.audio_control.unmute") as mock_unmute, \
         patch("src.audio_control.mute") as mock_mute:

        # If audio is already playing when F14/F22 is pressed, it must stop previous track first
        with patch.object(app.audio_player, "is_playing", return_value=True):
            app.on_f14_read_selected_only()
            mock_stop.assert_called_once()

        playback_event.wait(timeout=0.5)
        mock_start_queue.assert_called_with(b"AUDIO_DATA", is_last=True)
        mock_unmute.assert_called()
        mock_mute.assert_not_called()

def test_app_tts_f23_read_down(app):
    # 1. When IDLE (STOPPED) -> Select cursor down and stream
    with patch.object(app.audio_player, "is_playing", return_value=False), \
         patch.object(app.audio_player, "is_paused", return_value=False), \
         patch("src.app.copy_cursor_to_bottom", return_value="อ่านลงด้านล่าง") as mock_copy, \
         patch.object(app, "_stream_text_to_tts") as mock_stream:
        app.on_f23_single_click()
        mock_copy.assert_called_once()
        mock_stream.assert_called_with("อ่านลงด้านล่าง")

    # 2. When PLAYING -> Toggle Stop & unmute
    with patch.object(app.audio_player, "is_playing", return_value=True), \
         patch.object(app.audio_player, "stop") as mock_stop, \
         patch("src.app.audio_control.unmute") as mock_unmute:
        app.on_f23_single_click()
        mock_stop.assert_called_once()
        mock_unmute.assert_called_once()
        assert app._current_tts_session == 0.0

def test_app_tts_emergency_stop(app):
    with patch.object(app.audio_player, "stop") as mock_stop:
        app.emergency_stop_tts()
        mock_stop.assert_called_once()

def test_app_reload(app):
    with patch("src.audio_control.unmute") as mock_unmute, \
         patch.object(app.audio_player, "stop") as mock_stop:
        app.reload()
        mock_unmute.assert_called_once()
        mock_stop.assert_called_once()

def test_app_on_speed_change(app):
    with patch.object(app.tts_engine, "set_speed") as mock_set_speed:
        app.on_speed_change(1.5)
        mock_set_speed.assert_called_with(1.5)

def test_app_on_voice_change(app):
    with patch.object(app.tts_engine, "set_voice") as mock_set_voice:
        app.on_voice_change("th-TH-Standard-A")
        mock_set_voice.assert_called_with("th-TH-Standard-A")

def test_app_on_reset_usage(app):
    with patch.object(app.usage_tracker, "reset_stats") as mock_reset:
        app.on_reset_usage()
        mock_reset.assert_called_once()

def test_app_capture_monitors(app):
    with patch("src.app.capture_monitor_to_clipboard") as mock_cap:
        mock_cap.return_value = True
        app.on_capture_monitor_1()
        mock_cap.assert_called_with(1)

        app.on_capture_monitor_2()
        mock_cap.assert_called_with(2)

        app.on_capture_monitor_3()
        mock_cap.assert_called_with(3)

def test_app_f14_and_f15_reads(app):
    with patch("src.app.copy_selected_text", return_value="Selected text") as mock_sel, \
         patch.object(app, "_stream_text_to_tts") as mock_stream:
        app.on_f14_read_selected_only()
        mock_sel.assert_called_once()
        mock_stream.assert_called_with("Selected text")

    with patch("src.app.copy_cursor_to_bottom", return_value="Cursor down text") as mock_down, \
         patch.object(app, "_stream_text_to_tts") as mock_stream:
        app.on_f15_read_cursor_down()
        mock_down.assert_called_once()
        mock_stream.assert_called_with("Cursor down text")


def test_app_f20_live_toggle(app):
    with patch("src.app.LiveCopilotSession.start", return_value=True) as mock_start, \
         patch.object(app.tray_manager, "notify") as mock_notify:
        app.on_f20_live_toggle()
        time.sleep(0.05)
        mock_start.assert_called_once()
        mock_notify.assert_called_once()
        assert "ACTIVE" in mock_notify.call_args[0][1]


def test_app_f21_windows_local_tts_toggle_stop(app):
    # When already playing, F21 should stop immediately
    with patch.object(app.audio_player, "is_playing", return_value=True), \
         patch.object(app.audio_player, "stop") as mock_stop:
        app.on_f21_windows_local_tts()
        mock_stop.assert_called_once()


def test_app_f21_windows_local_tts_synthesis(app):
    # When stopped, F21 copies text and synthesizes locally
    with patch.object(app.audio_player, "is_playing", return_value=False), \
         patch.object(app.audio_player, "is_paused", return_value=False), \
         patch("src.app.copy_selected_text", return_value="ข้อความภาษาไทย") as mock_copy, \
         patch.object(app.local_tts_engine, "synthesize_to_bytes", return_value=b"LOCAL_WAV_BYTES") as mock_synth, \
         patch.object(app.audio_player, "play") as mock_play:

        app.on_f21_windows_local_tts()
        mock_copy.assert_called_once()
        time.sleep(0.05)  # Wait for worker thread
        mock_synth.assert_called_with("ข้อความภาษาไทย")
        mock_play.assert_called_with(b"LOCAL_WAV_BYTES")


def test_app_f16_read_down(app):
    # 1. When IDLE -> Read down from cursor
    with patch.object(app.audio_player, "is_playing", return_value=False), \
         patch.object(app.audio_player, "is_paused", return_value=False), \
         patch("src.app.copy_cursor_to_bottom", return_value="ข้อความอ่านลงล่าง") as mock_copy, \
         patch.object(app, "_stream_text_to_tts") as mock_stream:
        app.on_f16_single_click()
        mock_copy.assert_called_once()
        mock_stream.assert_called_with("ข้อความอ่านลงล่าง")

    # 2. When PLAYING -> Toggle Stop & unmute
    with patch.object(app.audio_player, "is_playing", return_value=True), \
         patch.object(app.audio_player, "stop") as mock_stop, \
         patch("src.app.audio_control.unmute") as mock_unmute:
        app.on_f16_single_click()
        mock_stop.assert_called_once()
        mock_unmute.assert_called_once()
        assert app._current_tts_session == 0.0


def test_app_f13_to_f20_preemption(app):
    """Test that pressing F20 while F13 STT is active immediately preempts and cancels STT to start Live Co-pilot."""
    # 1. Start F13 STT
    with patch("src.app.audio_control.mute"), \
         patch("threading.Thread"):
        app.toggle_stt()
        assert app.is_streaming is True

    # 2. Press F20 while STT is streaming -> STT must be aborted and Live Co-pilot started
    with patch("src.app.LiveCopilotSession.start", return_value=True) as mock_live_start:
        app.on_f20_live_toggle()
        for _ in range(25):
            if mock_live_start.called:
                break
            time.sleep(0.02)
        assert app.is_streaming is False
        mock_live_start.assert_called_once()


def test_app_f20_to_f13_preemption(app):
    """Test that pressing F13 while F20 Live is active immediately preempts Live and starts STT."""
    from unittest.mock import PropertyMock

    # 1. Simulate active Live Co-pilot
    with patch("src.app.LiveCopilotSession.start", return_value=True):
        app.on_f20_live_toggle()
        for _ in range(25):
            if app.live_session is not None:
                break
            time.sleep(0.02)

    assert app.live_session is not None
    with patch.object(type(app.live_session), "is_running", new_callable=PropertyMock, return_value=True), \
         patch.object(app.live_session, "stop", return_value=True) as mock_live_stop, \
         patch("src.app.audio_control.mute"), \
         patch("threading.Thread"):
        # 2. Press F13 -> Live must be stopped and STT started
        app.toggle_stt()
        assert app.is_streaming is True
        mock_live_stop.assert_called_once()


def test_app_f20_singleton_and_debounce(app):
    """Test strict singleton management: rapid F20 presses drop duplicates and second toggle stops session."""
    with patch("src.app.LiveCopilotSession.start", return_value=True) as mock_start, \
         patch("src.app.LiveCopilotSession.stop", return_value=True) as mock_stop:

        # 1. First F20 press launches session
        app.on_f20_live_toggle()
        for _ in range(25):
            if app.live_session is not None:
                break
            time.sleep(0.02)

        assert app.live_session is not None
        first_session = app.live_session
        assert mock_start.call_count == 1

        # 2. Immediate second F20 press within debounce window is dropped (no duplicate spawn)
        app.on_f20_live_toggle()
        time.sleep(0.05)
        assert app.live_session is first_session
        assert mock_start.call_count == 1

        # 3. Simulate session running and reset debounce timer for test
        first_session._is_running = True
        app._last_f20_press_time = 0.0

        # 4. Third F20 press toggles OFF the session cleanly
        app.on_f20_live_toggle()
        for _ in range(25):
            if app.live_session is None:
                break
            time.sleep(0.02)

        assert app.live_session is None
        mock_stop.assert_called()


def test_app_f20_toast_notification_debounce(app):
    """Test that desktop toast notifications are suppressed within 1.0s to avoid toast flooding."""
    with patch.object(app.tray_manager, "notify") as mock_notify:
        app._last_live_toast_time = 0.0
        app._last_live_toast_status = ""

        # 1. First toast should send
        app.notify_live_copilot_toast("ACTIVE (🟢 ON)")
        mock_notify.assert_called_once_with("Gemini Live Co-pilot", "Live Session: ACTIVE (🟢 ON)")

        # 2. Immediate second toast within 1.0s should be debounced and suppressed
        app.notify_live_copilot_toast("STOPPED (OFF)")
        assert mock_notify.call_count == 1

        # 3. Simulate elapsed time >= 1.0s
        app._last_live_toast_time = time.perf_counter() - 1.1

        # 4. Now the toast should be delivered
        app.notify_live_copilot_toast("STOPPED (OFF)")
        assert mock_notify.call_count == 2
        assert "STOPPED" in mock_notify.call_args[0][1]

        # 5. Redundant same-status toast even after 1.1s is suppressed
        app._last_live_toast_time = time.perf_counter() - 1.1
        app.notify_live_copilot_toast("STOPPED (OFF)")
        assert mock_notify.call_count == 2


