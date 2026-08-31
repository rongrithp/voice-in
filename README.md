# 🎙️ Voice Operating Hub Daemon (Voice-In)

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/pytest-155%20passed-brightgreen.svg)]()
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078D6.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()

An ultra-low latency, high-performance, two-way Windows voice operating hub daemon. Seamlessly converts speech to text directly at your cursor with **Real-time Interim Streaming Injection**, reads text aloud with studio-grade **Google Cloud Neural2-C TTS**, offers an interactive **Gemini Multimodal Live Co-pilot** with vision and voice barge-in, and features an **On-Screen Floating HUD** for continuous visual status feedback.

---

## 🌟 Key Features & Pipelines

### 1. ⚡ Real-Time Streaming STT (Instant Typing)
- **Instant Typing Injection:** Leverages Google Cloud Speech-to-Text streaming gRPC (`StreamingRecognize`) with `interim_results=True`. Text appears on screen character-by-character as you speak with near-zero latency.
- **Smart Delta Buffer:** Automatically detects monotonic word growth and appends deltas instantly; handles prefix revisions via atomic backspacing to prevent duplicate characters 100%.
- **Ultra-low Latency VAD Cutoff:** Client-side Voice Activity Detection finalizes and commits segments within **`280ms`** of silence.
- **Auto Master Audio Ducking:** Instantly mutes Windows Master Audio during speech capture to prevent feedback and ensure clean recording.

### 2. 🤖 Gemini Multimodal Live Co-pilot (Vision + Voice)
- **Interactive Two-Way Voice:** Powered by the Gemini Multimodal Live API (`gemini-3.1-flash-live-preview` / `gemini-2.5-flash`).
- **Live Multi-Monitor Vision:** Captures real-time screen frames from your active monitor (Monitor 1, 2, or 3) and streams them to Gemini.
- **Acoustic Echo Cancellation (AEC) & Barge-in:** Dynamic noise floor thresholding and AI speech reverberation protection prevent speakers from falsely triggering user barge-in.

### 3. 🔊 High-Fidelity Two-Way Text-to-Speech (TTS)
- **Google Cloud TTS:** Natural Thai female studio voices (`th-TH-Neural2-C` and `th-TH-Standard-A`).
- **Read Selected (F14):** Reads highlighted text immediately.
- **Read Down from Cursor (F15 / F16):** Selects from cursor to bottom (`Shift+Ctrl+End`), copies, and streams audio continuously.
- **Windows Native Local TTS (Offline 100%):** SAPI5 / OneCore fallback requiring zero internet connection.

### 4. 🖥️ Visual Interfaces & Cost Protection
- **On-Screen Floating Pill HUD Overlay:** Lightweight, frameless, topmost, click-through (`WS_EX_TRANSPARENT | WS_EX_NOACTIVATE`) status pill that alerts you whenever STT or Live Co-pilot is active to prevent API billing waste.
- **Persistent System Tray:** Real-time monthly usage and cost breakdown in Thai Baht (฿ THB), female voice selection, and speed controls.
- **Dashboard GUI:** CustomTkinter dashboard with live audio level meters, barge-in sensitivity slider, and live multi-monitor thumbnail previews.

---

## ⌨️ Hotkey Reference Table

| Hotkey | Pipeline / Action | Description | Behavior |
|:---|:---|:---|:---|
| **F13** | **Speak-to-Cursor STT** | Streams voice to text at cursor position | Toggle ON / OFF (Instant typing) |
| **F13 (Double-Click)** | **Emergency STT Abort** | Immediately aborts STT recording & unmutes audio | < 300ms double press |
| **F14** | **Read Selected Text** | Reads currently highlighted text via Cloud TTS | Single Click (Toggle Play/Pause) |
| **F15 / F16** | **Read Down from Cursor** | Selects text from cursor to bottom and reads aloud | Single Click (Toggle Play/Stop) |
| **F17** | **Capture Monitor 1** | Captures Monitor 1 full screen to clipboard | Single Click |
| **F18** | **Capture Monitor 2** | Captures Monitor 2 full screen to clipboard | Single Click |
| **F19** | **Capture Monitor 3** | Captures Monitor 3 full screen to clipboard | Single Click |
| **F20** | **Gemini Live Co-pilot** | Starts interactive Multimodal Live Session | Toggle ON / OFF |
| **F21** | **Windows Local TTS** | Reads selected text offline via Windows Native Voice | Toggle Play / Stop |

---

## 🎨 Visual Indicator States

### On-Screen Floating HUD Pill (`src/hud_overlay.py`)
Placed at the top-center of the primary screen with 100% click-through and zero focus stealing:
- **`🔴 [LIVE UPLOADING] STT ACTIVE • MIC ON  ▰▰▰▰`** : Google Cloud Speech STT gRPC is actively streaming PCM audio chunks with real-time VU level activity.
- **`🟢 [STREAMING DATA] GEMINI LIVE • MIC & VISION ACTIVE`** : Gemini Multimodal Live Co-pilot is actively connected with bidirectional audio & screen stream.
- **`🔊 [PLAYBACK ACTIVE] TTS READING...`** : Text-to-Speech audio playback is active.
- **Hidden / IDLE** : Window is completely hidden when no audio transmission is active.

### System Tray Status Badge (`src/tray_manager.py`)
- **🟢 Green Disc** : Voice Hub Ready / Idle.
- **🔴 Red Glowing Badge** : Active Data Transmission (STT / Gemini Live Stream).
- **⚠️ Amber Badge** : Warning / Configuration Error.

---

## 🏗️ Repository & Module Structure

```
07. voice-in/
├── config.py                  # Global configurations, hotkeys, thresholds, and voice settings
├── main.py                    # Daemon startup and lifecycle coordinator
├── requirements.txt           # Python package dependencies
├── README.md                  # Complete documentation
├── .gitignore                 # Strict secrets and cache exclusion rules
├── data/
│   └── .gitkeep               # Scaffolding directory for local usage tracking
├── src/
│   ├── actuator.py            # Win32 clipboard paste & StreamingTextInjector (Instant Typing)
│   ├── app.py                 # Central Voice Operating Hub App coordinator
│   ├── audio.py               # Sound capture, RMS metering, and live stream producers
│   ├── audio_control.py       # Windows Master Audio muting & ducking (pycaw)
│   ├── audio_player.py        # Streaming audio playback engine (pygame/sounddevice)
│   ├── gcp_speech_engine.py   # Real-time GCP Speech-to-Text streaming gRPC session
│   ├── gcp_tts_engine.py      # GCP Text-to-Speech client
│   ├── gui_dashboard.py       # CustomTkinter dashboard with live monitor thumbnails
│   ├── hud_overlay.py         # On-Screen floating HUD pill
│   ├── live_copilot.py        # Gemini Live Co-pilot with vision capture & AEC Barge-in
│   ├── live_gemini_engine.py  # Gemini Live API adapter
│   ├── live_memory.py         # Live session transcript memory tracker
│   ├── local_engine.py        # Local speech engine fallbacks
│   ├── router.py              # Engine routing and speech helpers
│   ├── sanitizer.py           # Text sanitizer & deduplication tracker
│   ├── screen_capture.py      # Multi-monitor high-speed screen capture (`mss`)
│   ├── tray_manager.py        # Windows system tray management (`pystray`)
│   ├── tts_engine.py          # Dual TTS pipeline coordinator
│   ├── usage_tracker.py       # Real-time API cost and usage calculator (THB)
│   ├── vad.py                 # WebRTC Voice Activity Detector
│   └── windows_local_tts.py   # Windows native SAPI5 / OneCore TTS
└── tests/                     # Comprehensive Unit & Integration Test Suite (155 tests)
```

---

## ⚙️ Configuration Reference (`config.py`)

| Parameter | Default | Description |
|:---|:---|:---|
| `STT_ENGINE` | `"gcp"` | STT backend (`gcp` / `local`) |
| `STT_MODE` | `"streaming"` | STT processing mode (`streaming` / `batch`) |
| `VAD_SILENCE_MS` | `280` | Silence duration (ms) before finalizing utterance |
| `RMS_THRESHOLD` | `250.0` | Microphone audio sensitivity floor |
| `GEMINI_LIVE_RMS_THRESHOLD`| `2500.0`| Barge-in user speech RMS trigger threshold |
| `GEMINI_LIVE_TARGET_MONITOR`| `1` | Default screen capture monitor index |
| `TTS_VOICE` | `"th-TH-Neural2-C"`| Default Google Cloud TTS voice |
| `TTS_SPEAKING_RATE` | `1.0` | Default speech speed (0.75x – 2.0x) |
| `DOUBLE_CLICK_THRESHOLD` | `0.30` | Single-click vs double-click window (seconds) |

---

## 🚀 Quick Start & Installation

### 1. Prerequisites
- Windows 10 or Windows 11 (64-bit)
- Python 3.11, 3.12, or 3.13
- Google Cloud Service Account with **Cloud Speech-to-Text** and **Cloud Text-to-Speech** APIs enabled.
- Gemini API Key (for Multimodal Live Co-pilot).

### 2. Setup Environment
```powershell
# Clone the repository
git clone https://github.com/rongrithp/voice-in.git
cd voice-in

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Credentials
- Place your `service_account.json` in the root directory.
- Create a `.env` file in the root directory:
```env
GEMINI_API_KEY=your_gemini_api_key_here
GOOGLE_APPLICATION_CREDENTIALS=service_account.json
```

### 4. Run the Daemon
```powershell
.\.venv\Scripts\python.exe main.py
```

---

## 🧪 Testing

Run the full pytest suite:

```powershell
.\.venv\Scripts\pytest.exe
```

**Verification Results:** `155 passed in ~10s (100% PASS)`
- Real-time interim streaming injection with delta buffer replacement
- Non-blocking hardware release on session stop
- Seamless switching between F13 (STT) and F20 (Live Co-pilot)
- Floating HUD Overlay and System Tray indicators
- Multi-monitor thumbnail grab and selection

---

## 📄 License
MIT License. Open-source for personal and commercial voice automation on Windows.
