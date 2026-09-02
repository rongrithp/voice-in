"""
Unit tests for Zero-UI Personal Co-pilot Session Orchestrator and Streaming Pipelines.
"""

import pytest
import asyncio
from src.zero_ui.contracts import (
    ClientHello,
    ClientMode,
    ClientCapabilities,
    SensorPayload,
    ImagePayload,
    AudioPayload,
    TelemetryPayload,
    SafetyFlag,
    PinoutGraph,
    ComponentDefinition,
    PinDefinition,
    SafetyRule,
    AbortFrame,
    AttachedDocumentPayload,
    UserPersonalizationConfig
)
from src.zero_ui.ground_truth import GroundTruthEngine
from src.zero_ui.fsm import ServerSessionState
from src.zero_ui.orchestrator import SessionOrchestrator


@pytest.fixture
def test_engine():
    engine = GroundTruthEngine(":memory:")
    graph = PinoutGraph(
        project_id="curing_cabinet_v1",
        schematic_version="1.0",
        components={
            "SSR_01": ComponentDefinition(
                id="SSR_01",
                name="Solid State Relay",
                pins={
                    "1": PinDefinition(pin_number="1", signal="AC_MAINS_220V", voltage_class="220V_AC", color_code="BROWN"),
                    "3": PinDefinition(pin_number="3", signal="DC_CONTROL", voltage_class="5V_DC", color_code="RED")
                }
            )
        },
        safety_rules=[
            SafetyRule(
                rule_id="RULE_BREAKER_ISOLATION",
                severity="CRITICAL_FATAL",
                condition="220V line",
                required_verification="CONFIRM_BREAKER_OFF"
            )
        ]
    )
    engine.register_graph(graph)
    return engine


def test_orchestrator_handshake(test_engine):
    async def _test():
        orchestrator = SessionOrchestrator(
            session_id="sess_01",
            project_id="curing_cabinet_v1",
            ground_truth_engine=test_engine
        )

        assert orchestrator.fsm.state == ServerSessionState.SESSION_INITIALIZED

        hello = ClientHello(client_id="edge_01", client_mode=ClientMode.EDGE_FIELD)
        resp = orchestrator.handle_client_hello(hello)

        assert resp["type"] == "SERVER_READY"
        assert resp["status"] == "ARMED"
        assert orchestrator.fsm.state == ServerSessionState.STANDBY_ARMED

    asyncio.run(_test())


def test_orchestrator_220v_safety_interlock(test_engine):
    async def _test():
        orchestrator = SessionOrchestrator(
            session_id="sess_02",
            project_id="curing_cabinet_v1",
            ground_truth_engine=test_engine
        )
        orchestrator.handle_client_hello(ClientHello(client_id="edge_01", client_mode=ClientMode.EDGE_FIELD))

        payload = SensorPayload(
            session_id="sess_02",
            sequence_id=1,
            image=ImagePayload(data="base64_frame"),
            audio_query=AudioPayload(data="base64_audio"),
            telemetry=TelemetryPayload(focus_locked=True)
        )

        chunks = []
        async for chunk in orchestrator.process_sensor_payload(payload, target_component_id="SSR_01", target_pin_id="1"):
            chunks.append(chunk)

        assert len(chunks) >= 1
        # First chunk must be high priority INTERLOCK_WARNING
        assert chunks[0].safety_flag == SafetyFlag.INTERLOCK_WARNING
        assert "220V" in chunks[0].text_transcript
        assert "breaker" in chunks[0].text_transcript
        assert orchestrator.fsm.state == ServerSessionState.AWAITING_PHYSICAL_ACK

    asyncio.run(_test())


def test_orchestrator_blurry_image_safety_halt(test_engine):
    async def _test():
        orchestrator = SessionOrchestrator(
            session_id="sess_03",
            project_id="curing_cabinet_v1",
            ground_truth_engine=test_engine
        )
        orchestrator.handle_client_hello(ClientHello(client_id="edge_01", client_mode=ClientMode.EDGE_FIELD))

        payload = SensorPayload(
            session_id="sess_03",
            sequence_id=2,
            image=ImagePayload(data="blurry_frame"),
            audio_query=AudioPayload(data="audio"),
            telemetry=TelemetryPayload(focus_locked=False)
        )

        chunks = []
        async for chunk in orchestrator.process_sensor_payload(payload):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0].safety_flag == SafetyFlag.STOP_PROBE_REQUIRED
        assert "multimeter" in chunks[0].text_transcript.lower() or "focus" in chunks[0].text_transcript.lower()
        assert orchestrator.fsm.state == ServerSessionState.AWAITING_PHYSICAL_ACK

    asyncio.run(_test())


def test_orchestrator_safe_grounded_streaming(test_engine):
    async def _test():
        async def mock_llm_stream(prompt: str, image_b64: str, audio_b64: str):
            yield "Connect wire to Pin 3."
            yield " Ensure polarity is positive."

        orchestrator = SessionOrchestrator(
            session_id="sess_04",
            project_id="curing_cabinet_v1",
            ground_truth_engine=test_engine,
            llm_engine_handler=mock_llm_stream
        )
        orchestrator.handle_client_hello(ClientHello(client_id="edge_01", client_mode=ClientMode.EDGE_FIELD))

        payload = SensorPayload(
            session_id="sess_04",
            sequence_id=3,
            image=ImagePayload(data="valid_frame"),
            audio_query=AudioPayload(data="audio"),
            telemetry=TelemetryPayload(focus_locked=True)
        )

        chunks = []
        async for chunk in orchestrator.process_sensor_payload(payload, target_component_id="SSR_01", target_pin_id="3"):
            chunks.append(chunk)

        assert len(chunks) == 2
        assert chunks[0].safety_flag == SafetyFlag.CLEAR
        assert "Pin 3" in chunks[0].text_transcript
        assert "positive" in chunks[1].text_transcript
        assert orchestrator.fsm.state == ServerSessionState.AWAITING_PHYSICAL_ACK

    asyncio.run(_test())


def test_orchestrator_ephemeral_storage_lifecycle(test_engine):
    async def _test():
        orchestrator = SessionOrchestrator(
            session_id="sess_ephemeral_01",
            project_id="curing_cabinet_v1",
            ground_truth_engine=test_engine
        )
        orchestrator.handle_client_hello(ClientHello(client_id="edge_01", client_mode=ClientMode.EDGE_FIELD))

        payload = SensorPayload(
            session_id="sess_ephemeral_01",
            sequence_id=10,
            image=ImagePayload(data="test_image_data"),
            audio_query=AudioPayload(data="test_audio_data"),
            telemetry=TelemetryPayload(focus_locked=True)
        )

        # Before processing, ephemeral buffer is empty
        assert orchestrator.ephemeral_buffer.current_image is None
        assert orchestrator.ephemeral_buffer.current_audio is None

        chunks = []
        async for chunk in orchestrator.process_sensor_payload(payload):
            chunks.append(chunk)

        # After processing turn completes, Ephemeral Working Memory is purged [INV-04]
        assert orchestrator.ephemeral_buffer.current_image is None
        assert orchestrator.ephemeral_buffer.current_audio is None
        assert len(chunks) >= 1
        # Tier 2 dialogue history recorded
        assert len(orchestrator.dialogue_history) == 1
        assert orchestrator.dialogue_history[0]["sequence_id"] == 10

    asyncio.run(_test())


def test_orchestrator_abort_frame_cancellation(test_engine):
    async def _test():
        async def slow_mock_llm_stream(prompt: str, image_b64: str, audio_b64: str):
            yield "Token 1"
            await asyncio.sleep(0.05)
            yield "Token 2"
            await asyncio.sleep(0.05)
            yield "Token 3"

        orchestrator = SessionOrchestrator(
            session_id="sess_abort_test",
            project_id="curing_cabinet_v1",
            ground_truth_engine=test_engine,
            llm_engine_handler=slow_mock_llm_stream
        )
        orchestrator.handle_client_hello(ClientHello(client_id="edge_01", client_mode=ClientMode.EDGE_FIELD))

        payload = SensorPayload(
            session_id="sess_abort_test",
            sequence_id=20,
            image=ImagePayload(data="valid_image"),
            audio_query=AudioPayload(data="valid_audio"),
            telemetry=TelemetryPayload(focus_locked=True)
        )

        chunks = []
        async for chunk in orchestrator.process_sensor_payload(payload):
            chunks.append(chunk)
            # Simulate edge local intent match in <50ms triggering ABORT_FRAME
            await orchestrator.handle_abort_frame(AbortFrame(session_id="sess_abort_test", sequence_id=20, reason="LOCAL_INTENT_MATCHED"))

        # Stream was aborted; subsequent tokens were cancelled
        assert len(chunks) == 1
        assert orchestrator._abort_event.is_set()
        assert orchestrator.ephemeral_buffer.current_image is None

    asyncio.run(_test())


def test_orchestrator_session_file_primacy_guard(test_engine):
    async def _test():
        captured_prompts = []

        async def inspect_llm_stream(prompt: str, image_b64: str, audio_b64: str):
            captured_prompts.append(prompt)
            yield "Document verified."

        orchestrator = SessionOrchestrator(
            session_id="sess_primacy_01",
            project_id="curing_cabinet_v1",
            ground_truth_engine=test_engine,
            llm_engine_handler=inspect_llm_stream
        )
        orchestrator.handle_client_hello(ClientHello(client_id="edge_01", client_mode=ClientMode.EDGE_FIELD))

        # Attach document [INV-06]
        doc = AttachedDocumentPayload(
            file_name="spec_sheet_v1.pdf",
            mime_type="application/pdf",
            size_bytes=4096,
            content_b64_or_text="CONFIDENTIAL PIN ASSIGNMENT: Pin 12 connects to Sensor VCC",
            priority_rank=1
        )
        orchestrator.attach_document(doc)

        payload = SensorPayload(
            session_id="sess_primacy_01",
            sequence_id=30,
            image=ImagePayload(data="frame"),
            audio_query=AudioPayload(data="audio"),
            telemetry=TelemetryPayload(focus_locked=True)
        )

        chunks = []
        async for chunk in orchestrator.process_sensor_payload(payload):
            chunks.append(chunk)

        assert len(captured_prompts) == 1
        assert "[INV-06] ATTACHED SESSION DOCUMENTS (PRIMARY GROUND TRUTH)" in captured_prompts[0]
        assert "spec_sheet_v1.pdf" in captured_prompts[0]
        assert "CONFIDENTIAL PIN ASSIGNMENT" in captured_prompts[0]

    asyncio.run(_test())


def test_orchestrator_synchronized_live_subtitles(test_engine):
    async def _test():
        async def mock_llm_stream(prompt: str, image_b64: str, audio_b64: str):
            yield "Hello"
            yield " World"

        personalization = UserPersonalizationConfig(
            persona_name="Personal Assistant",
            enable_live_subtitles=True
        )

        orchestrator = SessionOrchestrator(
            session_id="sess_subtitles_01",
            project_id="curing_cabinet_v1",
            ground_truth_engine=test_engine,
            llm_engine_handler=mock_llm_stream,
            personalization_config=personalization
        )
        orchestrator.handle_client_hello(ClientHello(client_id="edge_01", client_mode=ClientMode.EDGE_FIELD))

        payload = SensorPayload(
            session_id="sess_subtitles_01",
            sequence_id=40,
            image=ImagePayload(data="frame"),
            audio_query=AudioPayload(data="audio"),
            telemetry=TelemetryPayload(focus_locked=True)
        )

        chunks = []
        async for chunk in orchestrator.process_sensor_payload(payload):
            chunks.append(chunk)

        assert len(chunks) == 2
        # Verify dual-stream synchronized subtitle tokens [INV-07]
        assert chunks[0].subtitle_token == "Hello"
        assert chunks[1].subtitle_token == " World"

    asyncio.run(_test())

