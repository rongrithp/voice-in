import os
from dotenv import load_dotenv

load_dotenv()

# Hotkey Bindings (F13–F20 Dedicated Layout)
HOTKEY_STT = os.getenv("HOTKEY_STT", "f13").lower()             # Speak to cursor (F13)
HOTKEY_TTS_READ_SEL = os.getenv("HOTKEY_TTS_READ_SEL", "f14").lower()    # Read already selected text only (F14)
HOTKEY_TTS_READ_DOWN = os.getenv("HOTKEY_TTS_READ_DOWN", "f15").lower()   # Select from cursor to bottom and read (F15)
HOTKEY_TTS_TOGGLE = os.getenv("HOTKEY_TTS_TOGGLE", "f16").lower()      # Toggle Play / Pause (F16)
HOTKEY_CAP_MON1 = os.getenv("HOTKEY_CAP_MON1", "f17").lower()        # Capture Monitor 1 to Clipboard (F17)
HOTKEY_CAP_MON2 = os.getenv("HOTKEY_CAP_MON2", "f18").lower()        # Capture Monitor 2 to Clipboard (F18)
HOTKEY_CAP_MON3 = os.getenv("HOTKEY_CAP_MON3", "f19").lower()        # Capture Monitor 3 to Clipboard (F19)
HOTKEY_LIVE_COPILOT = os.getenv("HOTKEY_LIVE_COPILOT", "f20").lower() # Toggle Gemini Multimodal Live Co-pilot (F20)
KEY_LIVE_COPILOT_TOGGLE = "F20"
HOTKEY_WINDOWS_LOCAL_TTS = os.getenv("HOTKEY_WINDOWS_LOCAL_TTS", "f21").lower() # Windows Local Native TTS Offline (F21)
KEY_WINDOWS_LOCAL_TTS = "F21"

DEFAULT_TARGET_MONITOR = int(os.getenv("DEFAULT_TARGET_MONITOR", "1"))
GEMINI_LIVE_TARGET_MONITOR = int(os.getenv("GEMINI_LIVE_TARGET_MONITOR", "1"))  # Default: Monitor 1 (Primary Display)
GEMINI_LIVE_FPS = float(os.getenv("GEMINI_LIVE_FPS", "0.67")) # 1 frame per 1.5s (~0.67 FPS)
GEMINI_LIVE_FRAME_INTERVAL = float(os.getenv("GEMINI_LIVE_FRAME_INTERVAL", "1.5")) # 1.5s
GEMINI_LIVE_JPEG_QUALITY = int(os.getenv("GEMINI_LIVE_JPEG_QUALITY", "50")) # Quality 50
GEMINI_LIVE_MODEL = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
SHOW_VISION_PREVIEW = os.getenv("SHOW_VISION_PREVIEW", "True").lower() in ("true", "1", "yes")

# Backward compatible aliases
HOTKEY = HOTKEY_STT
HOTKEY_TTS = HOTKEY_TTS_READ_SEL
HOTKEY_TTS_READ = HOTKEY_TTS_READ_SEL
DOUBLE_CLICK_THRESHOLD = float(os.getenv("DOUBLE_CLICK_THRESHOLD", "0.30"))  # 300ms
DEBOUNCE_INTERVAL = float(os.getenv("DEBOUNCE_INTERVAL", "0.30"))            # 300ms debounce guard

# Google Cloud Credentials Auto-Discovery
def get_google_credentials_path() -> str:
    """
    Auto-discovers Google Cloud service account credentials in priority order:
    1. 'credentials.json' in workspace root
    2. 'service_account.json' in workspace root
    3. Environment variable GOOGLE_APPLICATION_CREDENTIALS
    """
    candidates = [
        "credentials.json",
        "service_account.json",
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")

# Primary STT Engine Configuration (Google Cloud Speech gRPC / Gemini / Local)
STT_ENGINE = os.getenv("STT_ENGINE", "gcp")  # Options: 'gcp' (Google Cloud Streaming STT), 'gemini-2.5-flash', 'local'
GOOGLE_APPLICATION_CREDENTIALS = get_google_credentials_path()
LANGUAGE_CODE = os.getenv("LANGUAGE_CODE", "th-TH")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
STT_MODE = os.getenv("STT_MODE", "streaming")  # Options: 'streaming' (Real-Time Interim Tokens < 200ms), 'batch'
MAX_CONTINUOUS_SPEECH_MS = int(os.getenv("MAX_CONTINUOUS_SPEECH_MS", "1800"))  # 1.8s continuous speech slice window
OVERLAP_MS = int(os.getenv("OVERLAP_MS", "300"))  # 300ms phonetic boundary overlap

# Audio & Mic Configuration
SAMPLE_RATE = 16000
CHANNELS = 1
MAX_QUEUE_SIZE = 10
RMS_THRESHOLD = float(os.getenv("RMS_THRESHOLD", "250.0"))

# Gemini Live Co-pilot Audio Noise Gate & VAD Calibration
GEMINI_LIVE_RMS_THRESHOLD = float(os.getenv("GEMINI_LIVE_RMS_THRESHOLD", "2500.0"))  # Calibrated noise floor gate (500-8000 range, default 2500)
GEMINI_LIVE_MIN_SPEECH_FRAMES = int(os.getenv("GEMINI_LIVE_MIN_SPEECH_FRAMES", "3"))  # 3 consecutive frames (~192ms) to confirm speech intent (150ms-200ms)


# Local faster-whisper Configuration (Low-Latency Local Dictation)
MODEL_SIZE = os.getenv("MODEL_SIZE", "small")  # Options: 'base', 'small'
DEVICE = os.getenv("DEVICE", "auto")           # Options: 'auto', 'cuda', 'cpu'
COMPUTE_TYPE = os.getenv("COMPUTE_TYPE", "auto") # Options: 'auto', 'float16', 'int8'
CPU_THREADS = int(os.getenv("CPU_THREADS", str(os.cpu_count() or 8)))
LOCAL_FILES_ONLY = os.getenv("LOCAL_FILES_ONLY", "False").lower() in ("true", "1", "yes")

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
VAD_SILENCE_MS = int(os.getenv("VAD_SILENCE_MS", "280"))         # Cut off audio after 280ms of continuous silence (Ultra-low latency 250-300ms)
MIN_SPEECH_DURATION_MS = int(os.getenv("MIN_SPEECH_DURATION_MS", "150")) # Discard audio segments shorter than 150ms
MIN_SPEECH_DURATION = float(os.getenv("MIN_SPEECH_DURATION", "0.15"))    # 0.15s (150ms)
STREAM_PARTIAL_INTERVAL_MS = int(os.getenv("STREAM_PARTIAL_INTERVAL_MS", "250")) # Continuous streaming slice interval (250ms)

# Text-to-Speech (TTS) Configuration (Google Cloud TTS Neural2 & Standard)
TTS_ENGINE = "gcp"
GCP_TTS_VOICE = os.getenv("GCP_TTS_VOICE", "th-TH-Neural2-C")
TTS_VOICE = GCP_TTS_VOICE
TTS_LANGUAGE_CODE = os.getenv("TTS_LANGUAGE_CODE", "th-TH")
TTS_SPEAKING_RATE = float(os.getenv("TTS_SPEAKING_RATE", "1.0"))
COPY_WAIT_SECONDS = float(os.getenv("COPY_WAIT_SECONDS", "0.08"))  # 80ms sleep between select & copy

FEMALE_VOICES = {
    "th-TH-Neural2-C": "Google Neural2-C (สตูดิโอ)",
    "th-TH-Standard-A": "Google Standard-A (คลาสสิก)",
}
