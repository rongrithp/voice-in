import io
import numpy as np
from scipy.io import wavfile
from google import genai
from google.genai import types
import config

SYSTEM_INSTRUCTION = """You are a high-precision Thai-English Speech-to-Text transcriber.
Rules:
1. Accurately transcribe mixed Thai and English technical terms.
2. Specific term spellings to prioritize:
   - "Voice-to-Cursor" (not "Voice ดูเคอร์เซอร์")
   - "Cloud" (not "คราว")
   - "Paste" (not "เพส")
   - "Real-time", "VS Code", "Python", "API", "Noise Gate", "Pipeline", "Latency", "Duplicate"
3. Output only verbatim spoken text. Never guess, predict, or add filler words when the audio is unclear or silent."""

import numpy as np
import config
from src.local_engine import LocalWhisperEngine

class TranscribeEngine:
    """Primary router for transcription services (defaults to Local faster-whisper)."""
    def __init__(self):
        self.engine = LocalWhisperEngine()

    def transcribe(self, audio_chunk: np.ndarray) -> str:
        return self.engine.transcribe(audio_chunk)

# Alias for backward compatibility
CloudTranscribeEngine = TranscribeEngine


