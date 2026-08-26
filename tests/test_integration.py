import time
import numpy as np
import pytest
from unittest.mock import patch, MagicMock
import config
from src.app import VoiceInjectorApp

def test_app_toggle():
    with patch("keyboard.add_hotkey"), patch("winsound.Beep"), patch("src.router.LocalWhisperEngine"):
        app = VoiceInjectorApp()
        assert app.is_streaming is False

        # Toggle ON
        with patch.object(app, "audio_worker"), patch.object(app, "stream_capture"):
            app.toggle()
            assert app.is_streaming is True

            # Toggle OFF
            app.toggle()
            assert app.is_streaming is False

def test_audio_worker_processing_flow():
    with patch("keyboard.add_hotkey"), patch("winsound.Beep"), patch("src.router.LocalWhisperEngine"):
        app = VoiceInjectorApp()
        app.is_streaming = True
        app.engine = MagicMock()
        app.engine.transcribe.return_value = "  ทดสอบ data flow   "

        with patch("src.app.inject_to_cursor") as mock_inject:
            audio_chunk = np.full(16000, 0.1, dtype=np.float32)
            app.audio_queue.put(audio_chunk)

            chunk = app.audio_queue.get(timeout=0.1)
            raw_text = app.engine.transcribe(chunk)
            from src.sanitizer import sanitize_text
            clean_text = sanitize_text(raw_text)
            mock_inject(clean_text)

            assert clean_text == "ทดสอบ data flow"
            mock_inject.assert_called_once_with("ทดสอบ data flow")
