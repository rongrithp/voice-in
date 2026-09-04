# Windows Edge: Cursor-Context Terminal Runner & Minimal HUD Painter

A high-performance, non-blocking execution actuator and cursor-anchored HUD subsystem for the VoiceHub / Voice-In desktop stack.

## Architecture

```
windows-edge/
├── requirements.txt         # Module dependencies (PyQt6, pywin32, sounddevice, numpy, faster-whisper)
├── hud_overlay.py          # Frameless, transparent, click-through HUD window
├── terminal_actuator.py    # Non-blocking subprocess runner with HUD pipe
├── server.py               # Local Event Bus HTTP server (127.0.0.1:8765)
├── audio_recorder.py       # Real-time 16kHz audio streamer & Push-to-Talk recorder
├── stt_engine.py           # Speech-to-Text engine (faster-whisper CPU int8)
├── intent_parser.py        # Bilingual (Thai/English) rule-based intent parser
├── intent_memory.py        # Adaptive human-in-the-loop memory & rule persistence
├── user_rules.json         # Dynamic persistent user-learned command mappings
├── visual_cortex.py        # Agent's Eye Sensory Module: Zero-disk window capture at cursor gaze
├── live_copilot_fsm.py     # Live Co-pilot FSM state-toggle (Wake <-> Standby) via F20
├── gemini_live_client.py   # Gemini Live Multimodal WebSocket client with Context Fusion
├── hotkey_listener.py      # Non-blocking global hotkey listener & voice mock
├── test_trigger.py         # Client verification script for Event Bus
├── main.py                 # Unified background launcher, test runner, and daemon
├── start_voice_in.bat      # One-click UTF-8 live voice service launcher
└── run_test.bat            # Automated verification test script
```

## HUD Modes (`hud_overlay.py`)

The HUD utilizes a full-screen, frameless, transparent, always-on-top window configured with native Win32 extended window styles (`WS_EX_TRANSPARENT`, `WS_EX_LAYERED`, `WS_EX_TOOLWINDOW`, `WS_EX_NOACTIVATE`) and PyQt flags (`WA_TranslucentBackground`, `WA_ShowWithoutActivating`, `WindowDoesNotAcceptFocus`) so that it **never steals user focus and never blocks mouse/keyboard clicks**.

### 1. `ACTION` Mode
- Retrieves current cursor coordinates `(x, y)` using `win32api.GetCursorPos()`.
- Renders a cybernetic glowing green target circle (**radius exactly 30px**) centered on the cursor with cardinal crosshairs, center anchor dot, and an optional outcome badge.
- Safely auto-closes after 2.5 seconds (or configured duration).

### 2. `THINKING` Mode
- Draws a semi-transparent dark rounded container offset **+20px** from current cursor coordinates.
- Displays high-contrast monospace text (Consolas / Cascadia) with syntax accents for cognitive anchoring during execution.
- Smart screen edge detection prevents overflow beyond monitor borders.
- Safely auto-closes after 2.5 seconds (or configured duration).

### 3. `CONFIRMATION` Mode (Adaptive Verification)
- Renders an amber target circle around the cursor and an interactive floating card:
  `เข้าใจว่า: [Action Name] | [Y] ใช่ / [N] ไม่ใช่ / [A] จำไว้เสมอ`
- Listens for global hotkeys without focus stealing:
  - **[Y]**: Execute command once.
  - **[N]**: Cancel execution and briefly show cancelled HUD.
  - **[A]**: Save phrase to `user_rules.json` with `auto_submit=True`, execute immediately, and bypass confirmation in future sessions.

## Subprocess Actuator (`terminal_actuator.py`)

- **Non-blocking Execution**: Dispatches terminal commands cleanly in background threads via `execute_async()` without blocking user input or UI loops.
- **Output & Exit Code Capture**: Captures return codes, stdout, and stderr with precision millisecond execution timing.
- **Immediate Outcome Piping**: Automatically spawns cognitive anchoring (`THINKING`) upon task launch, then triggers the glowing target circle (`ACTION`) upon completion displaying the outcome right next to the user's cursor.

## Quickstart & CLI Usage

### Run Tests
```powershell
# Via batch runner
.\windows-edge\run_test.bat

# Or via Python directly
python windows-edge/main.py --test
```

### Visual Demo
```powershell
python windows-edge/main.py --demo
```

### Direct HUD Execution
```powershell
# Action HUD
python windows-edge/hud_overlay.py --mode ACTION --text "Build Successful" --duration 2.5

# Thinking HUD
python windows-edge/hud_overlay.py --mode THINKING --text "> Running unit tests...\nAnalyzing..." --duration 2.5
```

### Terminal Actuator CLI
```powershell
python windows-edge/terminal_actuator.py --cmd "dir" --duration 2.5
```

### Global Hotkey Listener (Voice Trigger Mock)
```powershell
# Interactive background listener (Ctrl + Alt + Space)
python windows-edge/hotkey_listener.py

# Simulate trigger immediately
python windows-edge/hotkey_listener.py --simulate

# Run automated verification suite
python windows-edge/hotkey_listener.py --test
```

### Real-Time Audio Streamer & Push-to-Talk (`audio_recorder.py`)
```powershell
# Interactive Push-to-Talk (Hold Ctrl+Alt+Space to record, release to send)
python windows-edge/audio_recorder.py

# Run comprehensive microphone, STT, and intent verification test
python windows-edge/audio_recorder.py --test
```

### Speech-to-Text Engine (`stt_engine.py`)
```powershell
# Run automated STT engine verification test
python windows-edge/stt_engine.py --test

# Transcribe custom WAV audio
python windows-edge/stt_engine.py --wav path\to\audio.wav
```

### Natural Language Intent Parser (`intent_parser.py`)
```powershell
# Run automated intent unit test suite
python windows-edge/intent_parser.py --test

# Parse natural language phrase (Thai / English)
python windows-edge/intent_parser.py --text "เปิดบราวเซอร์"
python windows-edge/intent_parser.py --text "check git status"
```

### Adaptive Semantic Memory (`intent_memory.py`)
```powershell
# Run automated memory & pipeline verification tests
python windows-edge/intent_memory.py --test

# List stored user rules
python windows-edge/intent_memory.py --list

# Clear learned rules
python windows-edge/intent_memory.py --clear
```

### Live Voice-In Daemon Launcher
```powershell
# One-click batch launcher
.\windows-edge\start_voice_in.bat

# Or via Python directly
python windows-edge/main.py --live

# Run startup and health check
python windows-edge/main.py --live-check
```

### Visual Cortex (`visual_cortex.py`)
```powershell
# Run automated window screen capture test with 2s countdown
python windows-edge/visual_cortex.py --test

# Immediate capture and print window metadata JSON
python windows-edge/visual_cortex.py
```

### Live Co-pilot FSM & F20 State-Toggle (`live_copilot_fsm.py`)
```powershell
# Run automated F20 toggle & kill-switch verification suite
python windows-edge/live_copilot_fsm.py --test

# Run background F20 hotkey daemon
python windows-edge/live_copilot_fsm.py
```

### Gemini Live Multimodal Pipeline (`gemini_live_client.py`)
```powershell
# Run automated end-to-end multimodal verification test (Context Fusion + Audio Playback)
python windows-edge/gemini_live_client.py --test

# Ask Gemini Live about the active window under cursor with voice response
python windows-edge/gemini_live_client.py --query "หน้าต่างนี้คืออะไร สรุปสั้นๆ"
```

