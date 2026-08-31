import threading
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
from src.gcp_speech_engine import GCPSpeechEngine, GCPSpeechStreamSession
from src.router import TranscribeEngine

def test_gcp_speech_engine_init():
    engine = GCPSpeechEngine(credentials_path="service_account.json", language_code="th-TH")
    assert engine.language_code == "th-TH"

def test_gcp_speech_engine_warmup():
    engine = GCPSpeechEngine()
    engine.warmup()
    # Pure in-memory - does not initialize network client
    assert engine._client is None

def test_gcp_speech_engine_transcribe_mock():
    mock_speech = MagicMock()
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_alt = MagicMock()
    mock_alt.transcript = "สวัสดีครับ ทดสอบ Google Cloud STT"
    mock_result.alternatives = [mock_alt]
    mock_response = MagicMock()
    mock_response.results = [mock_result]
    mock_client.recognize.return_value = mock_response

    with patch.dict("sys.modules", {
        "google.cloud.speech_v1": mock_speech,
        "google.oauth2": MagicMock(),
        "google.oauth2.service_account": MagicMock()
    }):
        engine = GCPSpeechEngine()
        engine.client = mock_client
        audio = np.zeros(16000, dtype=np.float32)
        text = engine.transcribe(audio)
        assert text == "สวัสดีครับ ทดสอบ Google Cloud STT"

def test_gcp_speech_stream_session_send_and_stop():
    session = GCPSpeechStreamSession(on_token_callback=lambda t: None)
    with patch.object(session, "_run_grpc_stream", return_value=None):
        session.start()
        session.send_audio_chunk(b"\x00\x00" * 1600)
        session.stop()
        assert not session._running.is_set()

def test_gcp_speech_stream_recognition():
    emitted = []
    def on_token(t, is_final=False):
        emitted.append((t, is_final))
        if is_final:
            session._running.clear()

    session = GCPSpeechStreamSession(on_token_callback=on_token, client=None)
    session._running.set()

    mock_speech = MagicMock()
    mock_client = MagicMock()
    mock_res1 = MagicMock()
    mock_alt1 = MagicMock()
    mock_alt1.transcript = "สวัสดี"
    mock_res1.results = [MagicMock(alternatives=[mock_alt1], is_final=False)]

    mock_res2 = MagicMock()
    mock_alt2 = MagicMock()
    mock_alt2.transcript = "สวัสดีครับ"
    mock_res2.results = [MagicMock(alternatives=[mock_alt2], is_final=True)]

    mock_client.streaming_recognize.return_value = [mock_res1, mock_res2]
    session._client = mock_client

    with patch.dict("sys.modules", {
        "google.cloud.speech_v1": mock_speech,
        "google.oauth2": MagicMock(),
        "google.oauth2.service_account": MagicMock()
    }):
        session._run_grpc_stream()

    assert ("สวัสดี", False) in emitted
    assert ("สวัสดีครับ", True) in emitted

def test_gcp_speech_stream_interim_flushed_on_stop():
    emitted = []
    def on_token(t):
        emitted.append(t)

    session = GCPSpeechStreamSession(on_token_callback=on_token)
    session._last_interim_text = "ข้อความชั่วคราวตอนท้าย"
    session._running.set()
    session.stop()

    # Flushed cleanly on session stop
    assert "ข้อความชั่วคราวตอนท้าย" in emitted

def test_gcp_speech_stream_silence_vad_finalized():
    emitted = []
    def on_token(t):
        emitted.append(t)

    session = GCPSpeechStreamSession(on_token_callback=on_token)
    session._last_interim_text = "ประโยคที่พูดเสร็จแล้ว"
    session.speech_rms_threshold = 100.0
    session.silence_rms_threshold = 50.0
    session.frame_duration_ms = 30
    session._running.set()

    # 1. Send 1 voiced frame
    voiced_frame = (np.ones(480, dtype=np.int16) * 1000).tobytes()
    session.send_audio_chunk(voiced_frame)
    assert session._had_speech is True
    assert len(emitted) == 0

    # 2. Send 14 silence frames (14 * 30ms = 420ms >= 400ms)
    silence_frame = np.zeros(480, dtype=np.int16).tobytes()
    for _ in range(14):
        session.send_audio_chunk(silence_frame)

    assert "ประโยคที่พูดเสร็จแล้ว" in emitted

def test_transcribe_engine_defaults_to_gcp():
    with patch("src.gcp_speech_engine.GCPSpeechEngine") as mock_gcp_cls:
        engine = TranscribeEngine(engine_type="gcp")
        mock_gcp_cls.assert_called_once()

