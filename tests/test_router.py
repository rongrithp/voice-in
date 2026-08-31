import json
import numpy as np
import pytest
from unittest.mock import MagicMock, patch
import config
from src.local_engine import LocalWhisperEngine
from src.router import TranscribeEngine, GeminiTranscribeEngine, numpy_to_wav_bytes

def test_numpy_to_wav_bytes():
    audio = np.zeros(16000, dtype=np.float32)
    wav_bytes = numpy_to_wav_bytes(audio, sample_rate=16000)
    assert wav_bytes.startswith(b"RIFF")
    assert len(wav_bytes) > 44

    assert numpy_to_wav_bytes(None) == b""
    assert numpy_to_wav_bytes(np.array([])) == b""

def test_gemini_engine_transcribe_success():
    with patch("src.router.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "สวัสดีครับ ทดสอบ Gemini STT"}
                        ]
                    }
                }
            ]
        }
        mock_client.post.return_value = mock_response

        engine = GeminiTranscribeEngine(model_name="gemini-2.5-flash")
        audio = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio)

        assert result == "สวัสดีครับ ทดสอบ Gemini STT"
        mock_client.post.assert_called_once()

def test_gemini_engine_stream_transcribe():
    with patch("src.router.httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_lines.return_value = [
            'data: {"candidates": [{"content": {"parts": [{"text": "สวัสดี"}]}}]}',
            'data: {"candidates": [{"content": {"parts": [{"text": "ครับ"}]}}]}'
        ]
        mock_client.stream.return_value.__enter__.return_value = mock_response

        engine = GeminiTranscribeEngine(model_name="gemini-2.5-flash")
        audio = np.zeros(16000, dtype=np.float32)
        tokens = list(engine.stream_transcribe(audio))

        assert tokens == ["สวัสดี", "ครับ"]

def test_gemini_engine_empty_input():
    with patch("src.router.httpx.Client"):
        engine = GeminiTranscribeEngine()
        assert engine.transcribe(None) == ""
        assert engine.transcribe(np.array([])) == ""

def test_transcribe_engine_defaults_to_gemini():
    with patch("src.router.GeminiTranscribeEngine") as mock_gemini_cls:
        engine = TranscribeEngine(engine_type="gemini-2.5-flash")
        mock_gemini_cls.assert_called_once_with(model_name="gemini-2.5-flash")

def test_transcribe_engine_local_switch():
    with patch("src.local_engine.resolve_device_and_compute", return_value=("cpu", "int8")):
        engine = TranscribeEngine(engine_type="local")
        assert isinstance(engine.engine, LocalWhisperEngine)

def test_local_engine_init_params():
    mock_fw = MagicMock()
    with patch("src.local_engine.resolve_device_and_compute", return_value=("cpu", "int8")), \
         patch.dict("sys.modules", {"faster_whisper": mock_fw}):
        engine = LocalWhisperEngine(model_size="small", device="cpu", compute_type="int8")
        engine._model = None
        _ = engine.model
        mock_fw.WhisperModel.assert_called_once_with(
            "small",
            device="cpu",
            compute_type="int8",
            cpu_threads=engine.cpu_threads,
            local_files_only=False
        )

def test_local_engine_transcribe_success():
    mock_model = MagicMock()
    mock_segment = MagicMock()
    mock_segment.text = "สวัสดีครับ Voice-to-Cursor"
    mock_model.transcribe.return_value = ([mock_segment], None)

    with patch("src.local_engine.resolve_device_and_compute", return_value=("cpu", "int8")):
        engine = LocalWhisperEngine(device="cpu", compute_type="int8")
        engine.model = mock_model
        audio_chunk = np.zeros(16000, dtype=np.float32)
        result = engine.transcribe(audio_chunk)

        assert result == "สวัสดีครับ Voice-to-Cursor"
        mock_model.transcribe.assert_called_once_with(
            audio_chunk,
            language="th",
            initial_prompt=config.INITIAL_PROMPT,
            beam_size=config.BEAM_SIZE,
            vad_filter=config.VAD_FILTER,
            no_speech_threshold=config.NO_SPEECH_THRESHOLD,
            condition_on_previous_text=config.CONDITION_ON_PREVIOUS_TEXT,
            repetition_penalty=config.REPETITION_PENALTY
        )

def test_local_engine_transcribe_empty_audio():
    with patch("src.local_engine.resolve_device_and_compute", return_value=("cpu", "int8")):
        engine = LocalWhisperEngine(device="cpu", compute_type="int8")
        assert engine.transcribe(None) == ""
        assert engine.transcribe(np.array([])) == ""

def test_hallucination_filters():
    from src.router import is_prompt_leak_or_hallucination
    assert is_prompt_leak_or_hallucination("I'm not sure what you said.") is True
    assert is_prompt_leak_or_hallucination("I cannot understand the audio.") is True
    assert is_prompt_leak_or_hallucination("I can't hear any speech.") is True
    assert is_prompt_leak_or_hallucination("I'm sorry, could you repeat that?") is True
    assert is_prompt_leak_or_hallucination("As an AI, I am here to help.") is True
    assert is_prompt_leak_or_hallucination("You are an automated speech-to-text engine") is True
    assert is_prompt_leak_or_hallucination("สวัสดีครับ นี่คือเสียงภาษาไทย") is False
    assert is_prompt_leak_or_hallucination("This is actual speech transcription") is False
