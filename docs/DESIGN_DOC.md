# Engineering Design Document: Voice Operating Hub (voice-in)
**Status:** Active / Source of Truth  
**Role:** System Architecture & Integration Blueprint  

---

## 1. Overview & Objectives
Low-latency, resident Windows background daemon providing seamless multimodal voice input:
- **F13 STT Dictation:** Real-time speech-to-text with zero-destructive text injection.
- **F20 Gemini Live Co-pilot:** Full-duplex visual/voice conversational assistant.

### Non-Goals
- Supporting non-Windows operating systems in Phase 1.
- Long-term local retention of raw audio capture.

---

## 2. Core Architectural Invariants
1. **Strict Mutual Exclusivity (F13 XOR F20):**
   - At any timestamp T, only one mode may hold the audio capture hardware resource.
   - Cross-mode triggers execute preemptive hard-abort (< 50ms).
2. **Deterministic Lifecycle & Cleanup:**
   - Terminating a session mandates stopping the audio stream, clearing buffers, and cancelling all asyncio tasks.
   - Zero orphaned worker threads or ghost audio streams allowed.
3. **Append-Only Text Injection:**
   - Absolute ban on dynamic Backspace keystrokes during live transcription.
   - Output committed text only upon `is_final=True`.
4. **Acoustic Feedback Suppression:**
   - Microphone input must be suppressed/ducked while model audio playback is active to prevent echo loops.
5. **Input Keystroke Safety Invariant:**
   - All synthetic key injections must execute within `try...finally` guards ensuring explicit `key_up` release.
   - Preemptive aborts must trigger an emergency modifier release (`VK_CONTROL`, `VK_MENU`, `VK_SHIFT`, `VK_LWIN`, `VK_RWIN`) to prevent stuck keys.

---

## 3. System Topology & Data Flow
- **Presentation:** Minimal HUD Overlay (Top-Center Ultrawide Monitor).
- **Control Plane:** Unified Event Router & Finite State Machine (`IDLE`, `STT_ACTIVE`, `LIVE_ACTIVE`).
- **Data Plane:**
  - Google Speech-to-Text v2 Streaming API.
  - Gemini Live WebSocket / Bidi Streaming API.
  - Direct Win32 Keystroke Injection (`SendInput` Unicode payload).

---

## 4. Verification & Test Contracts
- **Unit Isolation:** All hardware mocks must verify `.close()` invocation upon session termination.
- **Preemption Contract:** Switching F13 <-> F20 must pass concurrency tests with zero deadlocks.
- **Keyboard State Integrity:** Interrupted key injection must leave zero stuck modifiers in OS keystate tables.
