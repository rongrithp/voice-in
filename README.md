# Voice Operating Hub Daemon (Voice-In)

An ultra-low latency, high-performance, two-way Windows voice operating hub daemon powered by **Google Cloud Speech-to-Text**, **Google Cloud Text-to-Speech**, **Gemini 2.5/3.1 Multimodal Live API**, and **Windows Native Offline TTS**.

---

## 🌟 Key Capabilities & Pipelines

| Hotkey | Pipeline | Engine / Architecture | Latency / Target |
|---|---|---|---|
| **F13** / **F21** | **Real-Time Streaming STT** | Google Cloud Speech gRPC (`StreamingRecognize`) with real-time interim streaming text injector | < 250ms VAD pause cutoff |
| **F14** | **TTS Read Selected Text** | Google Cloud Neural2-C Studio High-Fidelity Voice | Instant Async Playback |
| **F15 / F16** | **TTS Read Down from Cursor** | Selects down (`Shift+Ctrl+End`), copies, and streams to TTS | Continuous speech playback |
| **F17 / F18 / F19** | **Screen Capture to Clipboard** | High-speed multi-monitor grab for Monitor 1, 2, or 3 (`mss`) | Instant (< 50ms) |
| **F20** | **Gemini Multimodal Live Co-pilot** | Gemini Live API with real-time vision, bidirectional voice, and AEC Barge-in | Interactive voice & screen streaming |
| **F21 (Custom)** | **Windows Native Local TTS** | Windows SAPI5 / OneCore (100% Offline fallback) | 0ms network dependency |

---

## 🎨 Visual Interfaces & Monitoring

1. **On-Screen Floating HUD Overlay (`src/hud_overlay.py`):**
   - Lightweight, frameless, topmost, click-through (`WS_EX_TRANSPARENT | WS_EX_NOACTIVATE`) pill at the top of the screen.
   - **`🔴 STT LISTENING...`** (F13 Active)
   - **`🟢 GEMINI LIVE CONNECTED`** (F20 Active)
   - **`🔊 TTS READING...`** (F14/F16 Active)
   - Auto-hides when IDLE to keep the screen clean and prevent API cost burn.

2. **System Tray Integration (`src/tray_manager.py`):**
   - Persistent taskbar tray icon with dynamic warning states and live usage summaries.
   - Real-time monthly cost & usage breakdown (STT minutes and TTS characters in THB).
   - Voice selector (Female Neural2-C Studio / Standard-A) and speed slider (0.75x – 2.00x).
   - Emergency audio un-mute and daemon reload controls.

3. **Dashboard GUI (`src/gui_dashboard.py`):**
   - CustomTkinter dashboard with live audio level meters and multi-monitor live preview thumbnails.
   - Live target monitor switching for Gemini Live Co-pilot.
   - Barge-in RMS sensitivity slider and acoustic echo suppression controls.

---

## 🏗️ Project Architecture

```
07. voice-in/
├── config.py                  # Global configurations, hotkeys, thresholds, and voice settings
├── main.py                    # Daemon startup and lifecycle coordinator
├── requirements.txt           # Python package dependencies
├── data/
│   └── .gitkeep               # Usage stats storage scaffolding
├── src/
│   ├── actuator.py            # Win32 clipboard paste & StreamingTextInjector (Instant Typing)
│   ├── app.py                 # Central hub coordinator & hotkey dispatchers
│   ├── audio.py               # Audio capture, RMS calculation, and live streams
│   ├── audio_control.py       # Windows Master Audio muting & ducking (pycaw)
│   ├── audio_player.py        # Streaming audio playback engine (pygame/sounddevice)
│   ├── gcp_speech_engine.py   # Real-time GCP Speech-to-Text streaming gRPC session
│   ├── gcp_tts_engine.py      # GCP Text-to-Speech client
│   ├── gui_dashboard.py       # CustomTkinter settings dashboard with live monitor thumbnails
│   ├── hud_overlay.py         # On-Screen floating HUD pill
│   ├── live_copilot.py        # Gemini Live Co-pilot with vision capture & AEC Barge-in
│   ├── live_gemini_engine.py  # Gemini Live API client
│   ├── live_memory.py         # Live session transcript memory tracker
│   ├── local_engine.py        # Local speech engines
│   ├── router.py              # Engine routing and speech helpers
│   ├── sanitizer.py           # Text cleaner and deduplication memory
│   ├── screen_capture.py      # Multi-monitor high-speed screen capture
│   ├── tray_manager.py        # Windows system tray management (pystray)
│   ├── tts_engine.py          # Dual TTS pipeline coordinator
│   ├── usage_tracker.py       # Real-time API cost and usage calculator (THB)
│   ├── vad.py                 # WebRTC Voice Activity Detector
│   └── windows_local_tts.py   # Windows native SAPI5 / OneCore TTS
└── tests/                     # Comprehensive Unit & Integration Test Suite (155 tests)
```

---

## 🧪 Testing & Verification

Run the entire test suite via `pytest`:

```powershell
.\.venv\Scripts\pytest.exe
```

All 155 unit tests validate:
- Real-time interim streaming injection and delta text buffer replacement
- Non-blocking audio hardware release on session stops
- Seamless switching between F13 (STT) and F20 (Live Co-pilot)
- Floating HUD Overlay and System Tray indicators
- Multi-monitor thumbnail grab and selection

---

## 🚀 Running the Daemon

```powershell
.\.venv\Scripts\python.exe main.py
```
