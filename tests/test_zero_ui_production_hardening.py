"""
Production Hardening & Phase 5-6 Verification Tests for Zero-UI Real-Time Multimodal Personal Co-pilot.
Covers:
- 4-Stage Document Sanitizer (Magic bytes, Size Cap, Macro/Script Stripping, Plaintext Extraction)
- Edge Image Compressor (RAM Resize <=1280px & Compression)
- Client-Side Time-Stretching Audio Sink (WSOLA/OLA 0.75x-1.5x)
- RMS Noise Gate & Inactivity Watchdogs
- UserPersonalizationConfig Verbosity & Dynamic Directives
- UserRuntimeConfig Decoupling (.env / config.json)
- Monthly Usage & Cost Ledger (THB Currency)
- Edge Deep Standby (STANDBY_DORMANT) & Server Idle Watchdog
"""

import os
import io
import json
import time
import zipfile
import pytest

from src.zero_ui.contracts import (
    UserPersonalizationConfig,
    UserRuntimeConfig,
    AttachedDocumentPayload,
    TriggerSource,
    AudioConfig,
    PersonaConfig
)
from src.zero_ui.fsm import EdgeClientFSM, EdgeClientState
from src.zero_ui.sanitizer import DocumentSanitizer, DocumentSanitizationError
from src.zero_ui.media import (
    compress_image_frame,
    time_stretch_pcm,
    TimeStretchAudioSink,
    calculate_pcm_rms,
    RMSNoiseGate,
    ClientInactivityWatchdog
)
from src.zero_ui.ledger import UsageLedger, UsageRecord
from src.zero_ui.orchestrator import SessionOrchestrator
from src.zero_ui.ground_truth import GroundTruthEngine
from src.zero_ui.mock_edge_client import EdgeClientHarness


def test_document_sanitizer_whitelist_and_magic_bytes():
    # 1. Valid PDF with %PDF- header
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    s_bytes, s_mime, s_text = DocumentSanitizer.sanitize("test_doc.pdf", pdf_bytes)
    assert s_mime == "application/pdf"
    assert len(s_bytes) > 0

    # 2. Corrupted PDF (Magic bytes mismatch)
    with pytest.raises(DocumentSanitizationError) as exc_info:
        DocumentSanitizer.sanitize("corrupt.pdf", b"NOT_A_PDF_CONTENT")
    assert "Magic bytes mismatch" in str(exc_info.value)

    # 3. Valid Text / Markdown
    md_bytes = "# Circuit Diagram\nPin 1 connects to VCC.".encode("utf-8")
    s_bytes, s_mime, s_text = DocumentSanitizer.sanitize("notes.md", md_bytes)
    assert s_mime == "text/markdown"
    assert "Pin 1 connects to VCC." in s_text

    # 4. Disallowed file extension (.exe)
    with pytest.raises(DocumentSanitizationError) as exc_info:
        DocumentSanitizer.sanitize("malware.exe", b"MZ\x90\x00")
    assert "Unsupported file extension" in str(exc_info.value)


def test_document_sanitizer_security_stripping_and_limits():
    # 1. Script payload stripping in text/markdown
    script_injected_md = b"# Safe Title\n<script>alert('xss');</script>\nValid text."
    s_bytes, s_mime, s_text = DocumentSanitizer.sanitize("injected.md", script_injected_md)
    assert b"<script" not in s_bytes
    assert "Valid text." in s_text

    # 2. File size cap exceeding 25MB
    large_buffer = b"A" * (26 * 1024 * 1024)
    with pytest.raises(DocumentSanitizationError) as exc_info:
        DocumentSanitizer.sanitize("large.txt", large_buffer)
    assert "exceeds maximum allowed limit" in str(exc_info.value)

    # 3. OpenXML macro stripping (vbaProject.bin removal)
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types></Types>")
        zf.writestr("word/document.xml", "<w:document><w:body><w:p><w:t>Clean Document Text</w:t></w:p></w:body></w:document>")
        zf.writestr("word/vbaProject.bin", b"VBA_MACRO_PAYLOAD")

    docx_bytes = zip_buf.getvalue()
    s_bytes, s_mime, s_text = DocumentSanitizer.sanitize("test.docx", docx_bytes)
    assert "Clean Document Text" in s_text

    # Verify vbaProject.bin is stripped from output zip
    with zipfile.ZipFile(io.BytesIO(s_bytes), "r") as zf_out:
        assert "word/vbaProject.bin" not in zf_out.namelist()
        assert "word/document.xml" in zf_out.namelist()


def test_edge_image_compressor():
    from PIL import Image

    # Create synthetic large 2560x1440 image
    img = Image.new("RGB", (2560, 1440), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    large_bytes = buf.getvalue()

    # Compress to max_dim <= 1280
    compressed = compress_image_frame(large_bytes, max_dim=1280, quality=85)
    assert isinstance(compressed, bytes)
    assert len(compressed) < len(large_bytes)

    # Verify dimensions are scaled down
    out_img = Image.open(io.BytesIO(compressed))
    w, h = out_img.size
    assert max(w, h) <= 1280
    assert w == 1280
    assert h == 720


def test_time_stretching_audio_sink():
    # 24kHz 16-bit mono PCM sample (0.1 second = 2400 samples = 4800 bytes)
    sample_rate = 24000
    duration = 0.1
    num_samples = int(sample_rate * duration)
    # Generate simple sine wave
    import math, struct
    pcm_samples = [int(10000 * math.sin(2 * math.pi * 440 * i / sample_rate)) for i in range(num_samples)]
    raw_pcm = struct.pack(f"<{len(pcm_samples)}h", *pcm_samples)

    # 1. Normal 1.0x playback speed -> identity length
    sink_1x = TimeStretchAudioSink(sample_rate=sample_rate, playback_speed=1.0)
    out_1x = sink_1x.process_chunk(raw_pcm)
    assert len(out_1x) == len(raw_pcm)

    # 2. Faster 1.5x playback speed -> shorter output
    sink_1_5x = TimeStretchAudioSink(sample_rate=sample_rate, playback_speed=1.5)
    out_1_5x = sink_1_5x.process_chunk(raw_pcm)
    assert len(out_1_5x) < len(raw_pcm)

    # 3. Slower 0.75x playback speed -> longer output
    sink_0_75x = TimeStretchAudioSink(sample_rate=sample_rate, playback_speed=0.75)
    out_0_75x = sink_0_75x.process_chunk(raw_pcm)
    assert len(out_0_75x) > len(raw_pcm)


def test_rms_noise_gate_and_inactivity_watchdog():
    # 1. Pure silence (all zeros)
    silence_pcm = b"\x00\x00" * 1000
    assert calculate_pcm_rms(silence_pcm) == 0.0

    gate = RMSNoiseGate(threshold=0.015)
    assert gate.is_speech_active(silence_pcm) is False

    # 2. Active speech signal
    import struct
    loud_samples = [15000] * 1000
    loud_pcm = struct.pack(f"<{len(loud_samples)}h", *loud_samples)
    assert calculate_pcm_rms(loud_pcm) > 0.4
    assert gate.is_speech_active(loud_pcm) is True

    # 3. Inactivity Watchdog Timers
    silence_fired = False
    dormant_fired = False

    def on_silence():
        nonlocal silence_fired
        silence_fired = True

    def on_dormant():
        nonlocal dormant_fired
        dormant_fired = True

    watchdog = ClientInactivityWatchdog(
        vad_silence_timeout_sec=0.05,
        dormant_timeout_sec=0.10,
        on_silence_timeout=on_silence,
        on_dormant_timeout=on_dormant
    )

    watchdog.report_activity(is_speech=True)
    time.sleep(0.06)
    watchdog.check_timers()
    assert silence_fired is True
    assert dormant_fired is False

    time.sleep(0.06)
    watchdog.check_timers()
    assert dormant_fired is True


def test_personalization_verbosity_and_dynamic_instructions():
    engine = GroundTruthEngine(":memory:")

    # 1. ULTRA_CONCISE verbosity
    p_concise = UserPersonalizationConfig(
        persona_name="FieldTech",
        response_verbosity="ULTRA_CONCISE",
        custom_system_instructions="Always specify millimeter measurements."
    )
    orchestrator = SessionOrchestrator(
        session_id="sess_verb_01",
        project_id="p1",
        ground_truth_engine=engine,
        personalization_config=p_concise
    )
    prompt = orchestrator._build_grounded_system_prompt()
    assert "ULTRA_CONCISE" in prompt
    assert "Zero conversational filler" in prompt
    assert "Always specify millimeter measurements." in prompt
    assert "Contextual Dynamic Verbosity" in prompt

    # 2. DETAILED verbosity
    p_detailed = UserPersonalizationConfig(
        persona_name="Tutor",
        response_verbosity="DETAILED"
    )
    orchestrator.set_personalization(p_detailed)
    prompt_detailed = orchestrator._build_grounded_system_prompt()
    assert "DETAILED" in prompt_detailed
    assert "Provide comprehensive, step-by-step explanations" in prompt_detailed

    # 3. Dynamic runtime verbosity adjustment [INV-08 Revision]
    orchestrator.adjust_verbosity("ULTRA_CONCISE")
    prompt_adjusted = orchestrator._build_grounded_system_prompt()
    assert "ULTRA_CONCISE" in prompt_adjusted
    assert orchestrator.personalization.response_verbosity == "ULTRA_CONCISE"

    # 4. Configurable Persona Settings (default EXPERT_THINKING_OUT_LOUD)
    assert orchestrator.persona_config.style == "EXPERT_THINKING_OUT_LOUD"
    assert "Style: EXPERT_THINKING_OUT_LOUD" in prompt_adjusted

    orchestrator.set_persona_style("TACTICAL_FIELD_OPERATOR")
    prompt_style = orchestrator._build_grounded_system_prompt()
    assert "Style: TACTICAL_FIELD_OPERATOR" in prompt_style

    custom_persona = PersonaConfig(
        style="ANALYTICAL_EXPERT",
        custom_system_instruction="Always cross-reference capacitor voltage tolerances."
    )
    orchestrator.set_persona_config(custom_persona)
    prompt_custom = orchestrator._build_grounded_system_prompt()
    assert "Style: ANALYTICAL_EXPERT" in prompt_custom
    assert "Persona Instruction: Always cross-reference capacitor voltage tolerances." in prompt_custom


def test_user_runtime_config_loading(tmp_path):
    # 1. Load from dictionary
    cfg = UserRuntimeConfig.from_dict({
        "model_name": "gemini-2.5-pro",
        "client_playback_speed": 1.25,
        "session_idle_timeout_sec": 45.0
    })
    assert cfg.model_name == "gemini-2.5-pro"
    assert cfg.client_playback_speed == 1.25
    assert cfg.session_idle_timeout_sec == 45.0
    assert cfg.audio.session_idle_timeout_sec == 45.0
    assert cfg.audio.turn_silence_timeout_sec == 10.0   # default 10.0s
    assert cfg.turn_silence_timeout_sec == 10.0
    assert cfg.persona.style == "EXPERT_THINKING_OUT_LOUD"  # default persona style
    assert cfg.noise_gate_rms_threshold == 0.015  # default preserved
    assert cfg.rms_silence_timeout_sec == 5.0      # default 5.0s preserved
    assert cfg.thumbnail_dismiss_timeout_sec == 4.0  # default 4.0s preserved

    # 2. Load from JSON file with custom 2-stage timeouts, audio config, persona config
    cfg_file = tmp_path / "custom_config.json"
    cfg_file.write_text(json.dumps({
        "credentials_json_path": "creds.json",
        "vad_silence_timeout_sec": 5.0,
        "rms_silence_timeout_sec": 8.0,
        "thumbnail_dismiss_timeout_sec": 6.0,
        "audio": {
            "turn_silence_timeout_sec": 14.0,
            "session_idle_timeout_sec": 80.0
        },
        "persona": {
            "style": "ANALYTICAL_RESEARCHER",
            "custom_system_instruction": "Cite exact pin numbers."
        }
    }), encoding="utf-8")

    loaded_cfg = UserRuntimeConfig.load_from_file_or_env(str(cfg_file))
    assert loaded_cfg.credentials_json_path == "creds.json"
    assert loaded_cfg.vad_silence_timeout_sec == 5.0
    assert loaded_cfg.rms_silence_timeout_sec == 8.0
    assert loaded_cfg.thumbnail_dismiss_timeout_sec == 6.0
    assert loaded_cfg.audio.turn_silence_timeout_sec == 14.0
    assert loaded_cfg.turn_silence_timeout_sec == 14.0
    assert loaded_cfg.audio.session_idle_timeout_sec == 80.0
    assert loaded_cfg.session_idle_timeout_sec == 80.0
    assert loaded_cfg.persona.style == "ANALYTICAL_RESEARCHER"
    assert loaded_cfg.persona.custom_system_instruction == "Cite exact pin numbers."

    # 3. Environment Variable Override
    os.environ["VOICE_IN_PLAYBACK_SPEED"] = "1.35"
    os.environ["VOICE_IN_RMS_SILENCE_TIMEOUT_SEC"] = "6.5"
    os.environ["VOICE_IN_THUMBNAIL_DISMISS_TIMEOUT"] = "7.5"
    os.environ["VOICE_IN_TURN_SILENCE_SEC"] = "15.0"
    os.environ["VOICE_IN_SESSION_IDLE_SEC"] = "90.0"
    os.environ["VOICE_IN_PERSONA_STYLE"] = "CONCISE_MONOLOGUE"
    env_cfg = UserRuntimeConfig.load_from_file_or_env(str(cfg_file))
    assert env_cfg.client_playback_speed == 1.35
    assert env_cfg.rms_silence_timeout_sec == 6.5
    assert env_cfg.thumbnail_dismiss_timeout_sec == 7.5
    assert env_cfg.audio.turn_silence_timeout_sec == 15.0
    assert env_cfg.turn_silence_timeout_sec == 15.0
    assert env_cfg.audio.session_idle_timeout_sec == 90.0
    assert env_cfg.session_idle_timeout_sec == 90.0
    assert env_cfg.persona.style == "CONCISE_MONOLOGUE"
    del os.environ["VOICE_IN_PLAYBACK_SPEED"]
    del os.environ["VOICE_IN_RMS_SILENCE_TIMEOUT_SEC"]
    del os.environ["VOICE_IN_THUMBNAIL_DISMISS_TIMEOUT"]
    del os.environ["VOICE_IN_TURN_SILENCE_SEC"]
    del os.environ["VOICE_IN_SESSION_IDLE_SEC"]
    del os.environ["VOICE_IN_PERSONA_STYLE"]


def test_usage_ledger_cost_accounting(tmp_path):
    ledger_path = str(tmp_path / "test_usage_ledger.json")
    ledger = UsageLedger(
        ledger_path=ledger_path,
        input_rate_thb_per_m=2.50,
        output_rate_thb_per_m=10.00
    )

    # 1. Record session 1
    rec1 = ledger.record_usage(
        session_id="sess_001",
        client_id="android_field_1",
        input_tokens=100_000,
        output_tokens=20_000
    )
    # Cost = (100k/1M * 2.5) + (20k/1M * 10) = 0.25 + 0.20 = 0.45 THB
    assert rec1.estimated_cost_thb == 0.45

    # 2. Record session 2
    rec2 = ledger.record_usage(
        session_id="sess_002",
        client_id="pc_station_1",
        input_tokens=200_000,
        output_tokens=50_000
    )
    # Cost = (200k/1M * 2.5) + (50k/1M * 10) = 0.50 + 0.50 = 1.00 THB
    assert rec2.estimated_cost_thb == 1.00

    # 3. Query monthly summary
    summary = ledger.get_monthly_summary()
    assert summary["session_count"] == 2
    assert summary["total_input_tokens"] == 300_000
    assert summary["total_output_tokens"] == 70_000
    assert summary["total_cost_thb"] == 1.45
    assert summary["currency"] == "THB"


def test_deep_standby_and_edge_fsm_dormant():
    client = EdgeClientHarness()
    # Initial state
    assert client.fsm.state == EdgeClientState.BOOT_OFFLINE

    # Boot -> Connecting -> Ready
    client.fsm.transition_to(EdgeClientState.CONNECTING_CLOUD)
    client.fsm.transition_to(EdgeClientState.CONNECTED_READY)
    assert client.fsm.state == EdgeClientState.CONNECTED_READY

    # Enter Tier 3 Deep Standby
    assert client.enter_deep_standby() is True
    assert client.fsm.state == EdgeClientState.STANDBY_DORMANT

    # Wake from Deep Standby
    assert client.wake_from_deep_standby() is True
    assert client.fsm.state == EdgeClientState.CONNECTED_READY
