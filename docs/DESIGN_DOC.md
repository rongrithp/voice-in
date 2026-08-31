# RFC-0001: Architecture Specification for voice-in Daemon
**Subsystem:** Core Subsystem Architecture & Stream Pipeline  
**Target Platform:** Windows NT (x86_64)  
**Status:** PROPOSED / ACTIVE SPECIFICATION  

---

## 1. Design Principles (Unix & Kernel RFC)

1. **Rule of Separation (Mechanism vs Policy):**
   - Audio ingestion, speech synthesis/inference, and keystroke emission are separate, isolated pipes.
   - Routing policies do not leak into device drivers (PortAudio / Win32 API).
2. **Rule of Modularity (Do One Thing Well):**
   - `AudioCapture`: Ingest raw PCM (`16kHz, 16-bit, Mono`) -> produce byte chunks -> push to non-blocking FIFO.
   - `SpeechEngine / LiveEngine`: Consume PCM streams -> decode network payloads -> emit text/audio events.
   - `Actuator`: Consume UTF-8 strings -> synthesize atomic Win32 `SendInput` event packets -> flush.
3. **Deterministic Memory & Handle Lifecycles:**
   - Single-owner buffer semantics.
   - Strict RAII / Context Management over OS handles (PortAudio streams, Win32 event hooks).

---

## 2. Finite State Machine (FSM) Invariants

The daemon state space $S \in \{\text{IDLE}, \text{STT\_ACTIVE}, \text{LIVE\_ACTIVE}\}$ is strictly deterministic.

```
       +-------------------------------------------------------+
       |                                                       |
       v                                                       |
  +----------+           HotKey(F13)              +------------------+
  |          | ---------------------------------> |                  |
  |   IDLE   |                                    |    STT_ACTIVE    |
  |          | <--------------------------------- |   (F13 Stream)   |
  +----------+         HotKey(F13 Release)        +------------------+
       |                                                   |
       | HotKey(F20)                         HotKey(F20)   |
       |                                   (Preemption)    |
       v                                                   v
  +------------------------------------------------------------+
  |                        LIVE_ACTIVE                         |
  |                  (F20 Gemini Live Stream)                  |
  +------------------------------------------------------------+
       |
       | HotKey(F20 Release / ESC)
       v
  +----------+
  |   IDLE   |
  +----------+
```

### Transition Contracts:
- **Mutual Exclusivity ($F13 \oplus F20$):** Exactly one hardware capture stream can be open at any timestamp $T$.
- **Hard Preemption:** If an event arrives for mode $B$ while mode $A$ is active:
  1. Abort mode $A$ worker thread / task immediately ($t_{\text{abort}} < 50\text{ms}$).
  2. Flush and close audio input stream.
  3. Release all modifier keys (`VK_CONTROL`, `VK_MENU`, `VK_SHIFT`, `VK_LWIN`, `VK_RWIN`).
  4. Acquire lock and initialize mode $B$.

---

## 3. Data Flow & Pipeline Boundaries

```
[ Hardware Mic ]
       |
       v (PortAudio Callback / Non-blocking)
[ RingBuffer / Queue (16-bit PCM, 16kHz, Mono) ]
       |
       +----------------------------+
       | (F13 Active)               | (F20 Active)
       v                            v
[ GCP STT v2 Stream ]       [ Gemini Live Bidi WebSocket ]
       |                            |
       v (is_final=True only)       +-------------------+
[ Text Sanitizer ]                  |                   |
       |                            v (Audio PCM)       v (Text)
       v (Atomic SendInput)   [ AudioPlayer Sink ]   [ HUD Overlay ]
[ Win32 Keystroke Sink ]            |
                                    v (Ducking Active)
                            [ Inhibit Mic Ingest ]
```

---

## 4. Hardware & OS Subsystem Contracts

### 4.1 Audio Stream Subsystem (`src/audio.py`, `src/audio_player.py`)
- **Format:** `PCM_S16LE`, Sample Rate: `16000 Hz`, Channels: `1` (Mono), Frame Size: `512` samples.
- **Teardown Contract:** `stream.stop()` and `stream.close()` must be invoked explicitly in `finally` blocks.
- **Ducking / Echo Suppression:** While `AudioPlayer` is active, the ingest pipeline drops input buffers to prevent acoustic feedback loop without tearing down the connection.

### 4.2 OS Keystroke Injection Subsystem (`src/actuator.py`)
- **API Boundary:** Windows NT `user32.dll -> SendInput()`.
- **Zero Destructive Edits:** Ban on synthetic `VK_BACK` emissions.
- **Commit Boundary:** Strings are emitted strictly when speech tokens reach terminal state (`is_final == True`).
- **Emergency Teardown:** If an injection coroutine is cancelled or crashes:
  ```c
  /* Synthetic Modifier Release Invariant */
  INPUT inputs[5];
  // Explicit KEYEVENTF_KEYUP for VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU, VK_LSHIFT...
  SendInput(5, inputs, sizeof(INPUT));
  ```

### 4.3 Visual Feedback HUD (`src/hud_overlay.py`, `src/screen_border_overlay.py`)
- **Layering:** `WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW`.
- **Zero Input Theft:** Window must never acquire foreground focus (`WS_EX_NOACTIVATE`).

---

## 5. Teardown & Fault Isolation (Errno / Abort Specifications)

| Error Condition | Trigger | Kernel/Daemon Action | Teardown Path |
|---|---|---|---|
| `EPREEMPT` | Triggering F20 during active F13 | Preempt active STT task; abort gRPC stream | Flush queue -> Release modifiers -> Open Gemini WS |
| `EPIPE_RESET` | Remote WebSocket / gRPC Drop | Soft disconnect; notify HUD | Close transport -> Transition to `IDLE` |
| `EAUDIO_BUSY` | Audio device disconnected / locked | Log error -> fallback to IDLE | Release PortAudio context -> Restart audio device polling |
| `EINTR_ABORT` | SIGINT / Daemon shutdown | Execute global graceful drain | Drain queues -> Close streams -> Emit Win32 Key-Up -> Exit 0 |

---

## 6. Verification Test Matrix

1. **Device Mock Isolation:**
   - Unit tests must assert `mock_stream.close.assert_called_once()` under both clean exits and unhandled exceptions.
2. **Concurrency & Race Conditions:**
   - 100 iterations of rapid alternating F13/F20 triggers without deadlocks, lingering threads, or leaked open handles.
3. **OS Global Keystate Cleanliness:**
   - Verify `GetKeyState(VK_*)` and `GetAsyncKeyState(VK_*)` return `0` after aborted typing cycles.
