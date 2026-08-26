import os
from dotenv import load_dotenv

load_dotenv()

HOTKEY = 'f21'
SAMPLE_RATE = 16000
CHANNELS = 1
MAX_QUEUE_SIZE = 10

# Local faster-whisper Configuration (Intel NUC Optimized)
MODEL_SIZE = "small"  # Options: 'base', 'small'
DEVICE = "cpu"
COMPUTE_TYPE = "int8"
CPU_THREADS = 8
LOCAL_FILES_ONLY = True


# Language & Prompt Lock
LANGUAGE = "th"
INITIAL_PROMPT = "Voice-to-Cursor, Cloud, Paste, Real-time, VS Code, Python, API, Noise Gate, Pipeline, Latency, Duplicate, Intel NUC, WebRTC, VAD, faster-whisper"

# Transcription Parameters (Anti-Hallucination & CPU Speed Tuning)
BEAM_SIZE = 1
VAD_FILTER = True
NO_SPEECH_THRESHOLD = 0.6
CONDITION_ON_PREVIOUS_TEXT = False
REPETITION_PENALTY = 1.2

# WebRTC VAD Configuration
FRAME_DURATION_MS = 30       # 10, 20, or 30 ms
VAD_MODE = 3                 # Aggressiveness mode 3 (maximum noise suppression)
VAD_SILENCE_MS = 500         # Cut off audio after 500ms of continuous silence
MIN_SPEECH_DURATION_MS = 500 # Discard audio segments shorter than 500ms
