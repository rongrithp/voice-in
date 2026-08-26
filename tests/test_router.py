import numpy as np
import pytest
from unittest.mock import MagicMock, patch
import config
from src.local_engine import LocalWhisperEngine
from src.router import TranscribeEngine

def test_local_engine_init_params():
    with patch("src.local_engine.WhisperModel") as mock_whisper_cls:
        engine = LocalWhisperEngine(model_size="small")
        mock_whisper_cls.assert_called_once_with(
            "small",
            device="cpu",
            compute_type="int8",
            cpu_threads=8,
            local_files_only=True
        )

def test_local_engine_transcribe_success():
    with patch("src.local_engine.WhisperModel") as mock_whisper_cls:
        mock_model = MagicMock()
        mock_whisper_cls.return_value = mock_model

        mock_segment = MagicMock()
        mock_segment.text = "สวัสดีครับ Voice-to-Cursor"
        mock_model.transcribe.return_value = ([mock_segment], None)

        engine = LocalWhisperEngine()
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
    with patch("src.local_engine.WhisperModel"):
        engine = LocalWhisperEngine()
        assert engine.transcribe(None) == ""
        assert engine.transcribe(np.array([])) == ""
