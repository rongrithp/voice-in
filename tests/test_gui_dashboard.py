import pytest
from unittest.mock import MagicMock, patch
from PIL import Image
import config
from src.gui_dashboard import DashboardGUI
from src.tray_manager import TrayManager, DaemonStatus


@pytest.fixture(autouse=True)
def restore_config():
    orig = {
        "DEFAULT_TARGET_MONITOR": config.DEFAULT_TARGET_MONITOR,
        "GEMINI_LIVE_TARGET_MONITOR": config.GEMINI_LIVE_TARGET_MONITOR,
        "RMS_THRESHOLD": config.RMS_THRESHOLD,
        "GEMINI_LIVE_RMS_THRESHOLD": config.GEMINI_LIVE_RMS_THRESHOLD,
        "GEMINI_LIVE_BARGE_IN_THRESHOLD": getattr(config, "GEMINI_LIVE_BARGE_IN_THRESHOLD", 3500.0),
        "VAD_SILENCE_MS": config.VAD_SILENCE_MS,
        "STT_ENGINE": config.STT_ENGINE,
        "TTS_VOICE": config.TTS_VOICE,
        "GCP_TTS_VOICE": getattr(config, "GCP_TTS_VOICE", "th-TH-Neural2-C"),
        "TTS_SPEAKING_RATE": config.TTS_SPEAKING_RATE,
    }
    with patch("winsound.Beep"), \
         patch("google.genai.Client"):
        yield
    for k, v in orig.items():
        setattr(config, k, v)


def test_dashboard_gui_init():
    on_mon = MagicMock()
    on_rms = MagicMock()
    on_vad = MagicMock()
    on_stt = MagicMock()
    on_voice = MagicMock()
    on_speed = MagicMock()
    on_live = MagicMock()
    on_unmute = MagicMock()
    on_reset = MagicMock()
    on_exit = MagicMock()

    gui = DashboardGUI(
        on_target_monitor_change=on_mon,
        on_rms_threshold_change=on_rms,
        on_vad_silence_change=on_vad,
        on_stt_engine_change=on_stt,
        on_tts_voice_change=on_voice,
        on_tts_speed_change=on_speed,
        on_live_toggle=on_live,
        on_emergency_unmute=on_unmute,
        on_reset_usage=on_reset,
        on_exit=on_exit
    )

    assert gui.selected_monitor == getattr(config, "DEFAULT_TARGET_MONITOR", 1)
    assert gui.current_rms_threshold == getattr(config, "RMS_THRESHOLD", 250.0)
    assert gui.current_stt_engine == getattr(config, "STT_ENGINE", "gcp")
    assert gui.is_visible is False


def test_dashboard_gui_dynamic_settings_callbacks():
    on_mon = MagicMock()
    on_rms = MagicMock()
    on_barge_in = MagicMock()
    on_vad = MagicMock()
    on_stt = MagicMock()
    on_voice = MagicMock()
    on_speed = MagicMock()

    gui = DashboardGUI(
        on_target_monitor_change=on_mon,
        on_rms_threshold_change=on_rms,
        on_barge_in_threshold_change=on_barge_in,
        on_vad_silence_change=on_vad,
        on_stt_engine_change=on_stt,
        on_tts_voice_change=on_voice,
        on_tts_speed_change=on_speed
    )

    # 1. Target monitor change
    gui._on_select_monitor("Monitor 1")
    assert gui.selected_monitor == 1
    on_mon.assert_called_once_with(1)

    # 2. RMS threshold slider change
    gui._on_slider_threshold_change(350.0)
    assert gui.current_rms_threshold == 350.0
    on_rms.assert_called_once_with(350.0)

    # 2.1 Barge-in RMS noise gate slider change
    gui._on_slider_barge_in_threshold_change(3200.0)
    assert gui.current_barge_in_rms == 3200.0
    assert config.GEMINI_LIVE_RMS_THRESHOLD == 3200.0
    on_barge_in.assert_called_once_with(3200.0)

    # 3. VAD silence cutoff change
    gui._on_slider_vad_change(500.0)
    assert gui.current_vad_silence_ms == 500
    on_vad.assert_called_once_with(500)

    # 4. STT Engine selection
    gui._on_select_stt_engine("Gemini 2.5 Flash API")
    assert gui.current_stt_engine == "gemini-2.5-flash"
    on_stt.assert_called_once_with("gemini-2.5-flash")

    # 5. TTS Voice selection
    gui._on_select_tts_voice("th-TH-Standard-A (Classic Female)")
    assert gui.current_tts_voice == "th-TH-Standard-A"
    on_voice.assert_called_once_with("th-TH-Standard-A")

    # 6. TTS Speed selection
    gui._on_select_tts_speed("1.50x")
    assert gui.current_tts_speed == 1.50
    on_speed.assert_called_once_with(1.50)


def test_dashboard_gui_action_buttons():
    on_live = MagicMock()
    on_unmute = MagicMock()
    on_reset = MagicMock()
    on_exit = MagicMock()

    gui = DashboardGUI(
        on_live_toggle=on_live,
        on_emergency_unmute=on_unmute,
        on_reset_usage=on_reset,
        on_exit=on_exit
    )

    gui._on_click_live_toggle()
    on_live.assert_called_once()

    gui._on_emergency_unmute_clicked()
    on_unmute.assert_called_once()

    gui._on_reset_usage_clicked()
    on_reset.assert_called_once()

    gui._on_quit_clicked()
    on_exit.assert_called_once()


def test_dashboard_gui_show_hide_lifecycle():
    gui = DashboardGUI()
    mock_root = MagicMock()
    gui.root = mock_root

    # Show window
    gui.show()
    mock_root.after.assert_called()

    # Hide window (minimize to tray)
    gui.hide()
    mock_root.after.assert_called()

    # Update audio level
    gui.update_audio_level(450.0)
    mock_root.after.assert_called()

    # Update header status
    gui.update_status("Active", is_active=True)
    mock_root.after.assert_called()


def test_tray_manager_dashboard_menu_binding():
    on_dashboard = MagicMock()
    tray = TrayManager(on_open_dashboard=on_dashboard)

    # Simulate user clicking Settings & Dashboard
    tray._handle_open_dashboard(None, None)
    # The callback is invoked via thread
    import time
    time.sleep(0.05)
    on_dashboard.assert_called_once()


def test_app_dynamic_config_integration():
    from src.app import VoiceOperatingHubApp
    
    orig_mon = config.GEMINI_LIVE_TARGET_MONITOR
    orig_rms = config.RMS_THRESHOLD
    orig_live_rms = config.GEMINI_LIVE_RMS_THRESHOLD
    orig_barge_in = getattr(config, "GEMINI_LIVE_BARGE_IN_THRESHOLD", 3500.0)
    orig_vad = config.VAD_SILENCE_MS
    orig_stt = config.STT_ENGINE
    orig_speed = config.TTS_SPEAKING_RATE
    orig_voice = config.TTS_VOICE

    try:
        with patch("src.app.TrayManager"), \
             patch("src.app.DashboardGUI"), \
             patch("src.app.audio_control"), \
             patch("src.app.winsound"), \
             patch.object(VoiceOperatingHubApp, "_warmup_engines"):
            app = VoiceOperatingHubApp(start_gui=False)

            # Test dynamic monitor change
            app.on_target_monitor_change(3)
            assert config.GEMINI_LIVE_TARGET_MONITOR == 3
            assert app.live_copilot.target_monitor == 3

            # Test dynamic RMS threshold change
            app.on_rms_threshold_change(420.0)
            assert config.RMS_THRESHOLD == 420.0

            # Test dynamic Live Barge-in RMS threshold change
            app.on_barge_in_threshold_change(3100.0)
            assert config.GEMINI_LIVE_RMS_THRESHOLD == 3100.0
            assert app.live_copilot.noise_threshold == 3100.0

            # Test dynamic VAD silence change
            app.on_vad_silence_change(600)
            assert config.VAD_SILENCE_MS == 600

            # Test dynamic STT engine change
            app.on_stt_engine_change("gemini-2.5-flash")
            assert config.STT_ENGINE == "gemini-2.5-flash"
            assert app.engine.engine_type == "gemini-2.5-flash"

            # Test dynamic speed change
            app.on_speed_change(1.75)
            assert config.TTS_SPEAKING_RATE == 1.75
            assert app.tts_engine.speaking_rate == 1.75

            # Test dynamic voice change
            app.on_voice_change("th-TH-Standard-A")
            assert config.TTS_VOICE == "th-TH-Standard-A"
            assert app.tts_engine.voice_name == "th-TH-Standard-A"
    finally:
        config.GEMINI_LIVE_TARGET_MONITOR = orig_mon
        config.RMS_THRESHOLD = orig_rms
        config.GEMINI_LIVE_RMS_THRESHOLD = orig_live_rms
        config.GEMINI_LIVE_BARGE_IN_THRESHOLD = orig_barge_in
        config.VAD_SILENCE_MS = orig_vad
        config.STT_ENGINE = orig_stt
        config.TTS_SPEAKING_RATE = orig_speed
        config.TTS_VOICE = orig_voice


def test_dashboard_gui_multi_monitor_cards_selection():
    gui = DashboardGUI()
    gui._monitor_cards = {
        1: {"frame": MagicMock(), "select_btn": MagicMock()},
        2: {"frame": MagicMock(), "select_btn": MagicMock()},
        3: {"frame": MagicMock(), "select_btn": MagicMock()},
    }

    # Select Monitor 2
    gui._on_select_monitor(2)
    assert gui.selected_monitor == 2
    assert config.GEMINI_LIVE_TARGET_MONITOR == 2

    # Card 2 should be active, Card 1 should be inactive
    gui._monitor_cards[2]["frame"].configure.assert_called_with(border_color="#2ecc71")
    gui._monitor_cards[1]["frame"].configure.assert_called_with(border_color="#333333")


def test_dashboard_gui_update_preview_thumbnails():
    gui = DashboardGUI()
    mock_root = MagicMock()
    gui.root = mock_root
    gui._is_running = True
    gui._is_visible = True

    mock_thumb_lbl = MagicMock()
    gui._monitor_cards = {
        1: {"frame": MagicMock(), "thumb_lbl": mock_thumb_lbl, "select_btn": MagicMock(), "image_ref": None},
    }

    img = Image.new("RGB", (180, 100), color="blue")
    with patch("src.screen_capture.grab_monitor_thumbnail", return_value=img):
        gui._update_preview()
        mock_thumb_lbl.configure.assert_called()
        assert gui._monitor_cards[1]["image_ref"] is not None
        mock_root.after.assert_called()

