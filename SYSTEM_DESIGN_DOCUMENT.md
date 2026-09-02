# เอกสารการออกแบบทางวิศวกรรม (System Design Document - SDD)
**ชื่อโปรเจกต์:** Zero-UI Real-Time Multimodal Personal Co-pilot (Project Gemini)  
**เวอร์ชันระบบ:** 6.4 (PC Station & Android Edge Zero-UI Runtime Specifications)  
**สถานะ (Status):** ผ่านการทดสอบสมบูรณ์ 100% / อยู่ภายใต้ Code Freeze (57/57 Zero-UI Tests และ 234/234 Workspace Tests ผ่านทั้งหมด 100% ใน ~21.11s)  
**บทบาทหลัก (Core Role):** ระบบผู้ช่วยอัจฉริยะแบบมัลติโมดอลเรียลไทม์สองทางแบบไร้ส่วนติดต่อผู้ใช้ (Zero-UI Real-Time Multimodal AI: Project Gemini)  
**โครงสร้างพื้นฐาน (Topology):** Cloud-Centric Architecture ผ่าน Secure WebSockets (`wss://`) เข้ารหัส TLS เชื่อมต่อตรงสู่ Gemini Live  
**แพลตฟอร์มไคลเอนต์:** Android Edge Client (Headless Background Service) & PC Station Client  
**มาตรฐานทางวิศวกรรม:** RFC 2119 Specification Standard / Safety-Critical Multimodal Architecture / Decoupled Provider Adapter Pattern / Acoustic-First Speech Monologue Invariant  

---

## 1. ภาพรวมสถาปัตยกรรมและบทบาทระบบ (System Architecture & Core Role)

### 1.1 บทบาทหลัก (Core Role: Project Gemini)
**Project Gemini** คือระบบผู้ช่วยอัจฉริยะรับรู้มัลติโมดอล (Multimodal Ingestion) แบบสองทางอย่างแท้จริง ทำงานด้วยความหน่วงต่ำยิ่งยวด (Ultra-Low Latency < 350ms) ขับเคลื่อนด้วยโมเดล Gemini Multimodal Live API เป็นแกนหลัก โดยออกแบบให้ไร้ส่วนต่อประสานกับผู้ใช้ (Zero-UI) ในขณะปฏิบัติงานจริง ทั้งในชีวิตประจำวัน, งานเพิ่มผลผลิตบนคอมพิวเตอร์ (Desktop Productivity), และงานภาคสนามแบบเคลื่อนที่ (Mobile Fieldwork)

### 1.2 สถาปัตยกรรมคลาวด์และเลเยอร์ผู้ให้บริการโมเดลแบบแยกส่วน (Decoupled Provider Architecture)
ระบบใช้ **Model Provider Adapter Pattern** ผ่านอินเตอร์เฟซมาตรฐาน `BaseModelProvider`:
- แกนหลัก: **Google Gemini Multimodal Live API (`GeminiLiveAdapter`)** เชื่อมต่อตรงผ่าน `wss://`
- สำรองและออฟไลน์: **Open-Source / Local AI Pipelines (`OpenSourcePipelineAdapter`)** เช่น On-Device Gemma, vLLM, Ollama, Whisper, Piper, Kokoro

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 PROJECT GEMINI: ZERO-UI REAL-TIME ARCHITECTURE              │
└─────────────────────────────────────────────────────────────────────────────┘

 [CLIENT RUNTIMES]
 ┌───────────────────────────────────────┐
 │ Android Edge Client (Project Gemini): │
 │  ├── Headless Foreground Service      │
 │  ├── Configurable Wake-Word Engine    │
 │  │   (primary_word + aliases)         │
 │  ├── 2-Stage Timeout Policy [INV-10]  │
 │  │   ├── Stage 1: Turn-Silence (10.0s)│
 │  │   └── Stage 2: Idle Dormant (60.0s)│
 │  ├── ACTION_SEND Transient Quick-Drop │
 │  ├── Transient Image Thumbnail Overlay│
 │  │   ([INV-12]: 4.0s Auto-Dismiss/Tap)│
 │  └── Permission-Gated Telemetry [INV-05]
 └───────────────────┬───────────────────┘
                     │ [Direct Secure WebSocket wss:// TLS]
 ┌───────────────────┴───────────────────┐             ┌────────────────────────┐
 │ PC Station Client (Project Gemini):   │             │ CENTRAL CLOUD BACKEND  │
 │  ├── Configurable Wake-Word Engine    │             │ (wss:// Gateway)       │
 │  ├── Transient Quick-Drop Box         │             └───────────┬────────────┘
 │  │   (Alt+Space Dismiss-on-Enter)     │                         │
 │  ├── 2-Stage Timeout Policy [INV-10]  │                         │
 │  │   (Turn Seal 10s vs Screensaver 60s)│                         │
 │  ├── F13–F20 Ingestion Hotkey Map     │                         ▼
 │  │   (Non-Destructive Text F14/F15)   │             ┌────────────────────────┐
 │  │   (Local Clipboard Captures F17-19)│             │ SessionOrchestrator    │
 │  ├── Multi-Monitor (Display 1, 2, 3)  │             │  ├── PersonaConfig     │
 │  ├── Ultrawide Live Scaled Stream F20 │             │  │   (Style Override)  │
 │  ├── 4-Stage Document Sanitizer [INV-06]│           │  ├── Dynamic Verbosity │
 │  └── Audio Focus Ducking & Live Subs  │             │  ├── Speculative Pipe  │
 └───────────────────┬───────────────────┘             │  ├── 3-Tier Storage    │
                     │ [Direct Secure WebSocket wss:// TLS]                      ││
                     └──────────────────────────────────────────────────────────►││
                                                                                 ││
                               ┌─────────────────────────────────────────────────┴┘
                               │             MODEL PROVIDER FACTORY               │
                               ├────────────────────────┬─────────────────────────┤
                               │ GeminiLiveAdapter      │ OpenSourcePipelineAdapter│
                               │ (Google Gemini Live)   │ (On-Device Gemma/vLLM)  │
                               └────────────────────────┴─────────────────────────┘
```

---

## 2. กฎเกณฑ์สถาปัตยกรรมและข้อกำหนดใหม่ใน Project Gemini (Core Invariants)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│             กฎเกณฑ์สถาปัตยกรรมบังคับ (PROJECT GEMINI INVARIANTS)            │
├─────────────────────────────────────────────────────────────────────────────┤
│ [INV-01] Safety Interlock & Anti-Hallucination Grounding Guard              │
│ [INV-02] Finite State Machine (FSM) Lifecycle Strict Guard                  │
│ [INV-03] Zero-UI Hands-Free Real-Time Voice Streaming (<350ms)              │
│ [INV-04] Ephemeral Working Memory Invariant (Sensory Data In-RAM ONLY)       │
│ [INV-05] Permission-Gated Null-Safety Telemetry (Default None)               │
│ [INV-06] Session File Primacy Guard (4-Stage Sanitizer + Doc Primacy)       │
│ [INV-07] Opt-In Synchronized Live Subtitle Dual-Streaming & Audio Ducking   │
│ [INV-08] Acoustic-First Monologue: PersonaConfig & Dynamic Verbosity        │
│ [INV-09] Configurable Dynamic Wake-Word (No Hardcoded Keywords)             │
│ [INV-10] 2-Stage Timeout Policy (Turn-Silence 10.0s vs Session Idle 60.0s)  │
│ [INV-11] Transient Quick-Drop Box (Dismiss-on-Enter Zero-UI Overlay)        │
│ [INV-12] Android Transient Image Thumbnail Overlay (4.0s Dismiss / Tap Zoom)│
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 [INV-08 Revision] Acoustic-First Speech Monologue: PersonaConfig & Contextual Verbosity
- **PersonaConfig Schema:** กำหนด `PersonaConfig(style="EXPERT_THINKING_OUT_LOUD", custom_system_instruction=None)`
- รูปแบบการพูดของ Gemini ต้องจำลองลักษณะของ **ผู้เชี่ยวชาญที่กำลังคิดออกเสียงคนเดียว (Expert thinking out loud to oneself)**
- **ความยาวและความลึกตามบริบท (Contextual Dynamic Verbosity):** ยกเลิกการจำกัดความยาว 1–3 ประโยคแบบตายตัว สำหรับคำถามด่วนหรือข้อเท็จจริงสั้นๆ ให้ตอบกระชับ ตรงประเด็น สรุปใจความสำคัญมาก่อน (Bottom-line insight first) แต่หากผู้ใช้ร้องขอการอธิบายเชิงลึก, เรื่องเล่า, หรือการวิเคราะห์ที่ซับซ้อน ระบบสามารถขยายความยาวเชิงลึกและบรรยายได้อย่างสมบูรณ์โดยไม่ถูกตัดทอน
- **การปรับเปลี่ยนระดับความกระชับและสไตล์แบบไดนามิก (Runtime Style & Verbosity):** รองรับการปรับเปลี่ยนผ่าน `orchestrator.adjust_verbosity()` และ `orchestrator.set_persona_style()` หรือตัวแปรสภาพแวดล้อม `VOICE_IN_PERSONA_STYLE`
- **ข้อห้ามเด็ดขาด (Strict Locked Base Invariants):** ห้ามสร้างตาราง (Zero tables), ห้ามใช้ Markdown Formatting, ห้ามใช้ Bullet หรือ Nested List, และห้ามใส่คำทักทายเยิ่นเย้อ

### 2.2 [INV-09] Configurable Dynamic Wake-Word Engine
- นิยามสกีมา `WakeWordConfig` (`primary_word`, `aliases`, `sensitivity`, `model_path`)
- ห้ามเขียนคำสั่งดักจับ Wake-word แบบ Hardcoded ใน Audio Loop
- ค่าเริ่มต้นรองรับ `"gemini"`, `"hey gemini"`, `"ok gemini"` และปรับเปลี่ยนได้อิสระผ่านคอนฟิกหรือ Environment Variable

### 2.3 [INV-10 Revision 2] 2-Stage Timeout Architecture (`AudioConfig` & `TwoStageTimeoutFSM`)
- นิยามสกีมา `AudioConfig`:
  - **Stage 1 (Turn-Taking Seal):** `turn_silence_timeout_sec = 10.0s` รอ 10 วินาทีแห่งความเงียบก่อนทำการผนึกเทิร์นคำพูด (Seal Speech Turn) โดยที่ยังคงรักษาการเชื่อมต่อ WebSocket ไว้ในสถานะดักฟังเบา (Lightweight Listening State) เพื่อความต่อเนื่องของการสนทนา
  - **Stage 2 (Dormant Screensaver):** `session_idle_timeout_sec = 60.0s` หากไม่มีเสียงหรือกิจกรรมจากผู้ใช้เกิน 60 วินาที ระบบจะส่ง `EndStreamFrame(reason="SESSION_IDLE_TIMEOUT")`, ล้างบัฟเฟอร์เสียง และปรับสถานะ FSM สู่ `STANDBY_DORMANT` เพื่อประหยัดทรัพยากร
- รองรับการ Override ผ่านตัวแปรสภาพแวดล้อม: `VOICE_IN_TURN_SILENCE_SEC` และ `VOICE_IN_SESSION_IDLE_SEC`

### 2.4 [INV-11] Transient Quick-Drop Box
- **บน PC:** กล่องข้อความบรรทัดเดียวผ่านคีย์ลัด `Alt+Space` ปิดตัวเองอัตโนมัติเมื่อกด Enter (Dismiss-on-Enter) ส่งข้อความหรือ URL ตรงเข้าสู่ WebSocket Session โดยไม่ค้าง UI ไว้บนหน้าจอ
- **บน Android:** Intent Handler สำหรับ `ACTION_SEND` รับข้อความหรือลิงก์ที่แชร์มาจากแอปอื่นส่งตรงเข้าสู่เซสชัน Gemini ทันที

### 2.5 [INV-12] Android Transient Image Thumbnail Overlay
- เมื่อไคลเอนต์ทำการจับภาพหรือส่งเฟรมภาพ (Image Ingestion): แสดงภาพตัวอย่างขนาดเล็กแบบลอยตัวชั่วคราว (Transient Floating Thumbnail Preview)
- **Auto-Dismiss Lifecycle:** หากผู้ใช้ไม่ได้แตะต้องภายในเวลาที่กำหนด (ค่าเริ่มต้น **4.0 วินาที** ปรับตั้งค่าได้ผ่าน `thumbnail_dismiss_timeout_sec`) ภาพตัวอย่างจะเลือนหายอัตโนมัติ (Fade into background) สอดคล้องกับปรัชญา Zero-UI
- **Tap to Expand:** หากผู้ใช้แตะที่รูปภาพตัวอย่างก่อนหมดเวลา ระบบจะขยายเป็นภาพพรีวิวขนาดเต็มหน้าจอ (Full-size Preview Overlay) ทันที

---

## 3. สกีมาสัญญาข้อมูลระดับโพรโทคอล (Wire Protocol Schemas)

นิยามใน `src/zero_ui/contracts.py`:

### 3.1 `WakeWordConfig` ([INV-09])
```python
@dataclass
class WakeWordConfig:
    primary_word: str = "gemini"
    aliases: List[str] = field(default_factory=lambda: ["hey gemini", "ok gemini"])
    sensitivity: float = 0.5
    model_path: Optional[str] = None
```

### 3.2 `EndStreamFrame` ([INV-10])
```python
@dataclass
class EndStreamFrame:
    session_id: str
    reason: str = "RMS_SILENCE_TIMEOUT"
    timestamp_ns: int = field(default_factory=time.time_ns)
    type: str = "END_STREAM"
```

### 3.3 `QuickDropPayload` ([INV-11])
```python
@dataclass
class QuickDropPayload:
    content: str
    source: str = "PC_QUICK_DROP"  # "PC_QUICK_DROP" | "ANDROID_ACTION_SEND"
    timestamp_ns: int = field(default_factory=time.time_ns)
    type: str = "QUICK_DROP"
```

### 3.4 Wind Harmonics & Low-Frequency Elimination Filter ([INV-13])
ระบบกรองสัญญาณความถี่ต่ำแบบ Real-time High-Pass Butterworth Biquad ($Q = 0.7071$, $f_c = 80.0\text{ Hz}$, $f_s = 16000\text{ Hz}$) ตัดเสียงลมปะทะไมค์ (Wind Buffeting), เสียงลมหายใจพ่นใส่ไมโครโฟน (Breath Pops), และเสียงฮาร์มอนิกรบกวนจากลมแอร์/พัดลมที่มีความถี่ต่ำกว่า 80 Hz ออกจากสัญญาณเสียง PCM 16-bit 16kHz ก่อนส่งเข้าคำนวณ RMS Noise Floor และ Gemini Live Ingestion:
- **Zero-Dependency Core:** เขียนด้วย Pure Python & NumPy ทำงานได้ 100% บนทุกสภาพแวดล้อมโดยไม่ต้องพึ่งพา SciPy หรือ native BLAS DLL
- **State Preservation:** คงสถานะ delay registers ข้ามบล็อกสตรีมมิ่งต่อเนื่องเพื่อไม่ให้เกิดเสียงคลิก (Click-free continuous streaming)
- **Vocal Integrity:** รักษาย่านความถี่เสียงพูดมนุษย์ (> 100-3000 Hz) ให้ผ่านได้มากกว่า 95% ในขณะที่ลดทอนเสียงลมต่ำกว่า 80 Hz ได้มากกว่า 75%
- **Configurable Settings:** `ENABLE_WIND_FILTER = True`, `WIND_FILTER_CUTOFF_HZ = 80.0`

---

## 4. แผนผังคีย์ลัดของ PC Station Client (Project Gemini)

| คีย์ลัด (Hotkey) | Action Name | พฤติกรรมการทำงาน | นโยบายความปลอดภัยและ I/O |
| :--- | :--- | :--- | :--- |
| **Alt+Space** | `quick_drop` | เปิดกล่อง Transient Quick-Drop Box รับข้อความ/URL ส่งเข้าเซสชันและปิดตัวเองทันทีเมื่อกด Enter | User-Space Single-Line Overlay |
| **F13** | `talk_to_cursor` | สตรีมเสียงไมโครโฟนสด และแทรกข้อความถอดความต่อท้าย (Append-Only) ลงในตำแหน่งเคอร์เซอร์ | User-Space Text Injection |
| **F14** | `read_selected_text` | ดึงข้อความไฮไลต์และส่งสังเคราะห์เสียงผ่าน Cloud TTS | Non-Destructive (UI Automation / Stash & Restore) |
| **F15** | `read_below_text` | ดึงข้อความจากตำแหน่งเคอร์เซอร์/เมาส์จนถึงท้ายเอกสาร และส่งสังเคราะห์เสียง | Non-Destructive (UI Automation / Stash & Restore) |
| **F16** | `toggle_audio_playback` | สลับสถานะ เล่น / หยุดชั่วคราว / หยุดการทำงานของเสียง (Play/Pause/Halt) บน Audio Sink | Local Audio Focus & Sink Toggle |
| **F17** | `capture_display_1` | จับภาพหน้าจอ จอมอนิเตอร์ที่ 1 (Screen 0) และบันทึกลงใน OS Clipboard ทันที | Local OS Clipboard (DIB/BMP) |
| **F18** | `capture_display_2` | จับภาพหน้าจอ จอมอนิเตอร์ที่ 2 (Screen 1) และบันทึกลงใน OS Clipboard ทันที | Local OS Clipboard (DIB/BMP) |
| **F19** | `capture_display_3` | จับภาพหน้าจอ จอมอนิเตอร์ที่ 3 (Screen 2) และบันทึกลงใน OS Clipboard ทันที | Local OS Clipboard (DIB/BMP) |
| **F20** | `f20_display_selector` / `stream_ultrawide_live` | Transient Monitor Selector Overlay (`[1] Display 1`, `[2] Display 2`, etc.) เลือกจอด้วยปุ่มตัวเลขหรือคลิกเมาส์ แล้วเริ่มสตรีมทันที หากกำลังสตรีมอยู่ การกด F20 จะหยุดสตรีมทันที | Cloud Multimodal Ingestion Stream |

---

## 5. ผลการตรวจสอบและการจัดส่งมอบงาน (Verification & Sign-off)

- **Zero-UI Suite:** 57 / 57 ผ่านทั้งหมด 100% (Green)
- **Full Workspace Suite:** 235 / 235 ผ่านทั้งหมด 100% (Green ใน ~21.33 วินาที)
- **Code Freeze Status:** ACTIVE ครอบคลุมไดเรกทอรี `src/zero_ui/*` และเอกสารสถาปัตยกรรมทั้งหมด
