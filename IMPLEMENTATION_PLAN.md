# แผนการพัฒนาและติดตั้งระบบ (Implementation Plan)

**ชื่อโปรเจกต์:** Voice Operating Hub — Zero-UI Real-Time Multimodal AI (Project Gemini)  
**เวอร์ชันระบบ:** 6.4 (PC Station & Android Edge Zero-UI Runtime Specifications)  
**สถานะ (Status):** ดำเนินการและผ่านการตรวจสอบครบถ้วน (57/57 Zero-UI Tests และ 234/234 Workspace Tests ผ่านทั้งหมด 100%, Code Freeze Active)  
**ตำแหน่งไฟล์เป้าหมาย:** `G:\My Drive\07. voice-in\`  

---

## 1. ภาพรวมแผนผังการพัฒนา (Implementation Roadmap Overview)

ในเวอร์ชัน 6.3 ระบบได้รับการยกระดับนโยบายหมดเวลาและสไตล์เพอร์โซนา:
1. **2-Stage Timeout Architecture [INV-10 Revision 2]:** แทนที่ Single Silence Timeout ด้วย `AudioConfig` (`turn_silence_timeout_sec: 10.0s` ผนึกเทิร์นคำพูดโดยยังคงต่อสายดักฟังไว้แบบ Lightweight Listening และ `session_idle_timeout_sec: 60.0s` ตัดการเชื่อมต่อสู่ `STANDBY_DORMANT` เมื่อไม่มีกิจกรรม)
2. **Configurable Persona Settings [INV-08 Revision 2]:** เพิ่ม `PersonaConfig` ค่าเริ่มต้น `style="EXPERT_THINKING_OUT_LOUD"` รองรับการปรับแต่งผ่านคอนฟิกและ Environment Variable `VOICE_IN_PERSONA_STYLE` โดยที่ Base Acoustic Invariants ยังคงถูกล็อกไว้อย่างปลอดภัย
3. **Android Transient Image Thumbnail Overlay [INV-12]:** Floating thumbnail preview เมื่อส่งภาพ, auto-dismiss ใน 4.0s (Zero-UI) หรือ tap เพื่อ expand ดูภาพเต็ม
4. **Rebrand to Project Gemini:** เปลี่ยนการอ้างอิงระบบทั้งหมดจาก "Co-pilot" เป็น "Gemini"
5. **Configurable Dynamic Wake-Word:** เพิ่มสกีมา `WakeWordConfig` กำหนดคำปลุกแบบไดนามิก ปราศจาก Hardcoding

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 หมุดหมายการพัฒนาระบบ PROJECT GEMINI (MILESTONES)             │
├─────────────────────────────────────────────────────────────────────────────┤
│ Milestone 1: สัญญาข้อมูลมัลติโมดอล, Telemetry และ AbortFrame (เสร็จสมบูรณ์)   │
│ Milestone 2: การจัดการ 3-Tier Storage และ Ephemeral RAM [INV-04] (เสร็จสมบูรณ์)│
│ Milestone 3: Speculative Parallel Pipeline & Abort Cancellation (เสร็จสมบูรณ์)│
│ Milestone 4: Android Debounce, Reconnect Backoff & Telemetry Gate [INV-05] (เสร็จ)│
│ Milestone 5: PC Screen Capture, Drag-Drop [INV-06] & Audio Ducking [INV-07] (เสร็จ)│
│ Milestone 6: Latency SLA (<350ms), Hammering & Memory Leak-Free Profiling (เสร็จ)│
│ Milestone 7: 4-Stage Sanitizer, Time-Stretch Sink, RMS Gate & Watchdogs (เสร็จ)│
│ Milestone 8: Usage Ledger (THB), Systemd & Production Packaging (เสร็จสมบูรณ์) │
│ Milestone 9: F13 Talk-to-Cursor & Selected TTS Reader Integration (เสร็จสมบูรณ์)│
│ Milestone 10: Model Provider Adapter Pattern (Gemini & Open-Source) (เสร็จสมบูรณ์)│
│ Milestone 11: Non-Destructive Text Extraction & Local Clipboard Captures (เสร็จ)│
│ Milestone 12: Project Gemini Rebrand, Dynamic Wake-Word, Acoustic Monologue,│
│               RMS Silence Standby, และ Transient Quick-Drop Box (เสร็จสมบูรณ์)│
│ Milestone 13: Dynamic Verbosity (INV-08 Rev) & Configurable Silence Timeout │
│               (INV-10 Rev: rms_silence_timeout_sec 5.0s default) (เสร็จสมบูรณ์)│
│ Milestone 14: Android Transient Image Thumbnail Overlay [INV-12] (เสร็จสมบูรณ์)│
│ Milestone 15: 2-Stage Timeout Policy (AudioConfig 10s/60s) & PersonaConfig  │
│               (EXPERT_THINKING_OUT_LOUD) (55/55 Zero-UI ผ่าน 100%)          │
│ Milestone 16: PC Station F20 Display Selector, Desk Pill Minimal Capsule,    │
│               Android Pocket-Safe Double-Tap & Ongoing Notification Sync    │
│               (57/57 Zero-UI Tests และ 234/234 Workspace Tests ผ่าน 100%)    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. ตารางรายการคอมโพเนนต์ในระบบ (Component Inventory)

| ตำแหน่งไฟล์ | ชนิด | บทบาทและหน้าที่ | สถานะ |
| :--- | :--- | :--- | :--- |
| `src/zero_ui/contracts.py` | โพรโทคอลหลัก | Dataclasses สำหรับ Wire Protocol, `WakeWordConfig`, `EndStreamFrame`, `QuickDropPayload`, Runtime Config | **FROZEN** |
| `src/zero_ui/fsm.py` | ระบบสถานะ | State Machines ควบคุมการเปลี่ยนผ่านสถานะอย่างเข้มงวด + STANDBY_DORMANT (`current_state` property) | **FROZEN** |
| `src/zero_ui/ground_truth.py` | แกนความปลอดภัย | Context Ground Truth, Ephemeral RAM Policy [INV-04], และ Session File Primacy [INV-06] | **FROZEN** |
| `src/zero_ui/providers/base.py` | อินเตอร์เฟซโมเดล | `BaseModelProvider(ABC)` และ `ModelOutputChunk` มาตรฐาน Vendor-agnostic | **FROZEN** |
| `src/zero_ui/providers/gemini_live.py` | อะแดปเตอร์โมเดล | `GeminiLiveAdapter` เชื่อมต่อ Google Gemini Multimodal Live API | **FROZEN** |
| `src/zero_ui/providers/open_source_pipeline.py` | อะแดปเตอร์โมเดล | `OpenSourcePipelineAdapter` เชื่อมต่อ Open-Source / Local AI Pipelines (On-Device Gemma/vLLM) | **FROZEN** |
| `src/zero_ui/providers/factory.py` | แฟกทอรี | `ModelProviderFactory` สร้างและสลับ Provider อัตโนมัติ | **FROZEN** |
| `src/zero_ui/orchestrator.py` | ตัวเชื่อมต่อระบบ | ตัวประสานงานเซสชัน, Acoustic-First Monologue Invariant, 3-Tier Storage Lifecycle | **FROZEN** |
| `src/zero_ui/server.py` | เกตเวย์เซิร์ฟเวอร์ | CentralZeroUIServer รองรับ TLS (`wss://`), Session Watchdog, `END_STREAM`, `QUICK_DROP` | **FROZEN** |
| `src/zero_ui/mock_edge_client.py` | ไคลเอนต์ภาคสนาม | ไคลเอนต์ Android (Dynamic Wake-Word, RMS Teardown, `ACTION_SEND`, Dormant) | **FROZEN** |
| `src/zero_ui/station_client.py` | ไคลเอนต์เวิร์กสเตชัน | ไคลเอนต์ PC Station (Quick-Drop `Alt+Space`, Dynamic Wake-Word, RMS Teardown, F13–F20) | **FROZEN** |
| `src/zero_ui/sanitizer.py` | ความปลอดภัยเอกสาร | 4-Stage Document Sanitizer (Magic Bytes, Size Cap, Macro Strip, Text Extract) | **FROZEN** |
| `src/zero_ui/media.py` | การประมวลผลสื่อ | `DynamicRMSNoiseGate` (Adaptive Noise Floor), TimeStretchAudioSink (Play/Pause/Halt), ImageCompressor | **FROZEN** |
| `src/zero_ui/ledger.py` | บัญชีแยกประเภทต้นทุน | Usage Ledger คำนวณ Token และสรุปยอดค่าใช้จ่ายรายเดือนเป็นสกุลเงินบาท (THB) | **FROZEN** |
| `deploy/voice-in.service` | ดีพลอยเมนต์ | Systemd Service Template สำหรับระบบปฏิบัติการ Linux | **READY** |
| `deploy/start_daemon.*` | ดีพลอยเมนต์ | สคริปต์เริ่มต้นเซอร์วิสบน Linux/macOS (sh), Windows PowerShell (ps1) และ Batch (bat) | **READY** |
| `deploy/stop_daemon.bat` | ดีพลอยเมนต์ | สคริปต์หยุดการทำงานของเซอร์วิสและคืนทรัพยากรระบบทั้งหมด | **READY** |
| `tests/test_zero_ui_*.py` | ชุดการทดสอบ | ครอบคลุม 53 การทดสอบอัตโนมัติ Zero-UI (100% Green) | **PASSING** |
