"""
Unit tests for Zero-UI Real-Time Multimodal Personal Co-pilot Data Contracts, Wire Protocols, and Mobile Telemetry.
"""

import pytest
import json
from src.zero_ui.contracts import (
    ClientHello,
    ClientMode,
    ClientCapabilities,
    CaptureTriggerEvent,
    TriggerSource,
    SensorPayload,
    ImagePayload,
    AudioPayload,
    TelemetryPayload,
    ServerAudioStreamChunk,
    SafetyFlag,
    StateUpdateEvent,
    StepStatus,
    PinoutGraph,
    ComponentDefinition,
    PinDefinition,
    SafetyRule
)


def test_client_hello_serialization():
    hello = ClientHello(
        client_id="android_edge_01",
        client_mode=ClientMode.EDGE_FIELD,
        capabilities=ClientCapabilities(camera_pdaf=True, max_image_resolution=[3840, 2160]),
        auth_token="token_secret_123"
    )
    json_str = hello.to_json()
    parsed = json.loads(json_str)

    assert parsed["type"] == "CLIENT_HELLO"
    assert parsed["client_id"] == "android_edge_01"
    assert parsed["client_mode"] == "EDGE_FIELD"
    assert parsed["capabilities"]["camera_pdaf"] is True

    # Deserialize back
    restored = ClientHello.from_json(json_str)
    assert restored.client_id == hello.client_id
    assert restored.client_mode == ClientMode.EDGE_FIELD
    assert restored.capabilities.camera_pdaf is True


def test_capture_trigger_event():
    event = CaptureTriggerEvent(
        trigger_source=TriggerSource.BT_MEDIA_BUTTON,
        timestamp_ns=1725178000000000,
        action="CAPTURE_SNAPSHOT_AND_LISTEN",
        context_hint="vanilla_curing_ssr_panel"
    )
    json_str = event.to_json()
    restored = CaptureTriggerEvent.from_json(json_str)

    assert restored.trigger_source == TriggerSource.BT_MEDIA_BUTTON
    assert restored.timestamp_ns == 1725178000000000
    assert restored.context_hint == "vanilla_curing_ssr_panel"


def test_sensor_payload_serialization():
    payload = SensorPayload(
        session_id="sess_001",
        sequence_id=1,
        image=ImagePayload(format="image/jpeg", data="base64_image_bytes"),
        audio_query=AudioPayload(format="audio/pcm;rate=16000;channels=1;bits=16", data="base64_audio_bytes"),
        telemetry=TelemetryPayload(focus_locked=True, lux_level=550.5)
    )
    json_str = payload.to_json()
    restored = SensorPayload.from_json(json_str)

    assert restored.session_id == "sess_001"
    assert restored.image.data == "base64_image_bytes"
    assert restored.audio_query.data == "base64_audio_bytes"
    assert restored.telemetry.focus_locked is True
    assert restored.telemetry.lux_level == 550.5


def test_server_audio_stream_chunk():
    chunk = ServerAudioStreamChunk(
        session_id="sess_001",
        chunk_index=0,
        is_final=False,
        data="raw_pcm_audio_chunk",
        safety_flag=SafetyFlag.INTERLOCK_WARNING,
        text_transcript="Warning: Confirm main breaker is off before touching terminal 1."
    )
    json_str = chunk.to_json()
    restored = ServerAudioStreamChunk.from_json(json_str)

    assert restored.chunk_index == 0
    assert restored.is_final is False
    assert restored.safety_flag == SafetyFlag.INTERLOCK_WARNING
    assert "breaker" in restored.text_transcript


def test_state_update_event():
    event = StateUpdateEvent(
        current_step_id="STEP_01_SSR_WIRING",
        step_description="Wire Pin 1 to AC Hot In",
        status=StepStatus.AWAITING_PHYSICAL_CONFIRMATION,
        verified_ground_truth_ref="SCHEMATIC_V2_SHEET_1"
    )
    json_str = event.to_json()
    restored = StateUpdateEvent.from_json(json_str)

    assert restored.current_step_id == "STEP_01_SSR_WIRING"
    assert restored.status == StepStatus.AWAITING_PHYSICAL_CONFIRMATION


def test_pinout_graph_ground_truth():
    graph = PinoutGraph(
        project_id="vanilla_cabinet",
        schematic_version="2.1",
        components={
            "SSR_01": ComponentDefinition(
                id="SSR_01",
                name="Solid State Relay",
                pins={
                    "1": PinDefinition(pin_number="1", signal="AC_HOT_IN", voltage_class="220V_AC", color_code="BROWN"),
                    "2": PinDefinition(pin_number="2", signal="AC_HEATER_OUT", voltage_class="220V_AC", color_code="BLACK"),
                    "3": PinDefinition(pin_number="3", signal="DC_CONTROL_POS", voltage_class="3-32V_DC", color_code="RED", target_component="ESP32", target_pin="GPIO_18"),
                    "4": PinDefinition(pin_number="4", signal="DC_CONTROL_NEG", voltage_class="GND", color_code="BLUE", target_component="PSU_12V", target_pin="GND"),
                }
            )
        },
        safety_rules=[
            SafetyRule(
                rule_id="RULE_220V_MAIN_BREAKER",
                severity="CRITICAL_FATAL",
                condition="Accessing Pin 1 or Pin 2",
                required_verification="CONFIRM_BREAKER_OFF"
            )
        ]
    )

    assert graph.is_high_voltage("SSR_01", "1") is True
    assert graph.is_high_voltage("SSR_01", "2") is True
    assert graph.is_high_voltage("SSR_01", "3") is False
    assert graph.is_high_voltage("SSR_01", "4") is False

    pin_info = graph.get_connection_ground_truth("SSR_01", "3")
    assert pin_info is not None
    assert pin_info.target_component == "ESP32"
    assert pin_info.target_pin == "GPIO_18"


def test_device_telemetry_permissions_and_null_safety():
    from src.zero_ui.contracts import (
        GpsCoordinates,
        MotionTelemetry,
        DeviceHealthTelemetry
    )

    # 1. Null-safety & defaults when permissions not granted
    default_telem = TelemetryPayload()
    assert default_telem.gps is None
    assert default_telem.motion is None
    assert default_telem.device_health is None
    assert default_telem.permissions_granted["location"] is False

    # 2. Populated opt-in telemetry
    full_telem = TelemetryPayload(
        focus_locked=True,
        lux_level=620.0,
        gps=GpsCoordinates(latitude=13.7563, longitude=100.5018, altitude=15.2, accuracy_meters=3.5),
        motion=MotionTelemetry(acceleration_x=0.02, acceleration_y=0.98, acceleration_z=0.12, user_activity="WALKING"),
        device_health=DeviceHealthTelemetry(battery_level=0.88, is_charging=False, thermal_status="NORMAL"),
        permissions_granted={"location": True, "motion": True, "camera": True, "microphone": True}
    )

    payload = SensorPayload(
        session_id="sess_telemetry_test",
        sequence_id=42,
        image=ImagePayload(data="mock_img"),
        audio_query=AudioPayload(data="mock_aud"),
        telemetry=full_telem
    )

    json_str = payload.to_json()
    restored = SensorPayload.from_json(json_str)

    assert restored.telemetry.gps is not None
    assert restored.telemetry.gps.latitude == 13.7563
    assert restored.telemetry.gps.longitude == 100.5018
    assert restored.telemetry.motion.user_activity == "WALKING"
    assert restored.telemetry.device_health.battery_level == 0.88
    assert restored.telemetry.permissions_granted["location"] is True


def test_personal_copilot_spec_contracts():
    from src.zero_ui.contracts import (
        AbortFrame,
        AttachedDocumentPayload,
        UserPersonalizationConfig,
        TriggerSource,
        LockScreenIndicatorStatus,
        ServerAudioStreamChunk
    )

    # 1. AbortFrame serialization (<50ms intent match cancel)
    abort = AbortFrame(session_id="sess_abort_01", sequence_id=5, reason="LOCAL_INTENT_MATCHED")
    abort_json = abort.to_json()
    restored_abort = AbortFrame.from_json(abort_json)
    assert restored_abort.session_id == "sess_abort_01"
    assert restored_abort.reason == "LOCAL_INTENT_MATCHED"

    # 2. AttachedDocumentPayload ([INV-06] Session File Primacy Guard)
    doc = AttachedDocumentPayload(
        file_name="circuit_schematic.pdf",
        mime_type="application/pdf",
        size_bytes=1048576,
        content_b64_or_text="JVBERi0xLjQK...",
        priority_rank=1
    )
    doc_json = doc.to_json()
    restored_doc = AttachedDocumentPayload.from_json(doc_json)
    assert restored_doc.file_name == "circuit_schematic.pdf"
    assert restored_doc.priority_rank == 1

    # 3. UserPersonalizationConfig
    config = UserPersonalizationConfig(
        persona_name="Executive Assistant",
        tone_directive="Warm and ultra-concise",
        enable_live_subtitles=True
    )
    config_json = config.to_json()
    restored_config = UserPersonalizationConfig.from_json(config_json)
    assert restored_config.persona_name == "Executive Assistant"
    assert restored_config.enable_live_subtitles is True

    # 4. Live Subtitle Dual-Streaming Chunk ([INV-07])
    chunk = ServerAudioStreamChunk(
        session_id="sess_sub_01",
        chunk_index=1,
        is_final=False,
        data="mock_pcm",
        subtitle_token="Hello"
    )
    chunk_json = chunk.to_json()
    restored_chunk = ServerAudioStreamChunk.from_json(chunk_json)
    assert restored_chunk.subtitle_token == "Hello"

    # 5. Lock screen shutter trigger & indicator status enums
    assert TriggerSource.FLOATING_SHUTTER_BUTTON.value == "FLOATING_SHUTTER_BUTTON"
    assert LockScreenIndicatorStatus.LISTENING.value == "LISTENING"


