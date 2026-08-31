import pytest
from unittest.mock import patch, MagicMock
from src.windows_local_tts import (
    WindowsNativeTTSEngine,
    clean_local_tts_text,
    list_installed_windows_voices
)


def test_clean_local_tts_text():
    assert clean_local_tts_text("") == ""
    assert clean_local_tts_text("### Header 1\nHello [world](https://example.com) *test*") == "Header 1 Hello world test"
    assert clean_local_tts_text("Visit https://google.com now") == "Visit now"


def test_list_installed_windows_voices():
    voices = list_installed_windows_voices()
    assert isinstance(voices, list)
    if voices:
        assert "id" in voices[0]
        assert "name" in voices[0]
        assert "is_thai" in voices[0]


def test_windows_native_tts_engine_init_and_selection():
    mock_voices = [
        {"id": "token_en", "name": "Microsoft David", "is_onecore": False, "is_thai": False},
        {"id": "token_th", "name": "Microsoft Pattara - Thai", "is_onecore": True, "is_thai": True},
    ]
    with patch("src.windows_local_tts.list_installed_windows_voices", return_value=mock_voices):
        engine = WindowsNativeTTSEngine()
        # Should auto-select Thai voice
        assert engine._voice_token_id == "token_th"
        assert "Pattara" in engine.get_voice_name()

        # Test preferred voice selection
        engine.set_voice("David")
        assert engine._voice_token_id == "token_en"


def test_windows_native_tts_rate_calculation():
    engine = WindowsNativeTTSEngine(speed=1.0)
    assert engine._calculate_sapi_rate() == 0

    engine.set_speed(1.5)
    assert engine._calculate_sapi_rate() == 4

    engine.set_speed(2.0)
    assert engine._calculate_sapi_rate() == 8

    engine.set_speed(0.75)
    assert engine._calculate_sapi_rate() == -2


def test_windows_native_tts_synthesize_to_bytes_empty():
    engine = WindowsNativeTTSEngine()
    assert engine.synthesize_to_bytes("") == b""
    assert engine.synthesize_to_bytes("   ") == b""


def test_windows_native_tts_synthesize_to_bytes_mock():
    mock_sp_voice = MagicMock()
    mock_mem_stream = MagicMock()
    mock_mem_stream.GetData.return_value = b"\x00\x00" * 100

    mock_win32 = MagicMock()
    mock_win32.Dispatch.side_effect = lambda prog_id: {
        "SAPI.SpVoice": mock_sp_voice,
        "SAPI.SpMemoryStream": mock_mem_stream,
        "SAPI.SpAudioFormat": MagicMock(),
        "SAPI.SpObjectToken": MagicMock()
    }.get(prog_id, MagicMock())

    with patch("win32com.client.Dispatch", side_effect=mock_win32.Dispatch), \
         patch("pythoncom.CoInitialize"), \
         patch("pythoncom.CoUninitialize"):
        engine = WindowsNativeTTSEngine()
        wav = engine.synthesize_to_bytes("สวัสดีชาวโลก")
        assert wav.startswith(b"RIFF")
        assert len(wav) >= 44 + 200
        mock_sp_voice.Speak.assert_called_once()
