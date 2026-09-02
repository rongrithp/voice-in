"""
End-to-End System Integration Tests for Zero-UI Real-Time Multimodal Personal Co-pilot.
Tests live WebSocket communication between Cloud Gateway Server, Edge Mobile Client, and PC Workstation Client.
"""

import asyncio
import json
import pytest
import websockets
from src.zero_ui.contracts import (
    PinoutGraph,
    ComponentDefinition,
    PinDefinition,
    SafetyRule,
    SafetyFlag,
    TriggerSource,
    ClientHello,
    ClientMode,
    AbortFrame,
    AttachedDocumentPayload,
    UserPersonalizationConfig
)
from src.zero_ui.ground_truth import GroundTruthEngine
from src.zero_ui.server import CentralZeroUIServer, ZeroUIServer
from src.zero_ui.mock_edge_client import EdgeClientHarness
from src.zero_ui.station_client import StationIngestionClient


@pytest.fixture
def populated_ground_truth():
    engine = GroundTruthEngine(":memory:")
    graph = PinoutGraph(
        project_id="vanilla_curing_cabinet",
        schematic_version="2.1-verified",
        components={
            "SSR_01": ComponentDefinition(
                id="SSR_01",
                name="Solid State Relay 40A",
                pins={
                    "1": PinDefinition(pin_number="1", signal="220V_MAINS_HOT_IN", voltage_class="220V_AC", color_code="BROWN"),
                    "2": PinDefinition(pin_number="2", signal="220V_HEATER_OUT", voltage_class="220V_AC", color_code="BLACK"),
                    "3": PinDefinition(pin_number="3", signal="DC_INPUT_POS", voltage_class="5V_DC", color_code="RED", target_component="ESP32", target_pin="GPIO_18"),
                    "4": PinDefinition(pin_number="4", signal="DC_INPUT_NEG", voltage_class="GND", color_code="BLUE")
                }
            )
        },
        safety_rules=[
            SafetyRule(
                rule_id="RULE_MAIN_BREAKER",
                severity="CRITICAL_FATAL",
                condition="220V connection",
                required_verification="CONFIRM_BREAKER_OFF"
            )
        ]
    )
    engine.register_graph(graph)
    return engine


def test_e2e_edge_field_mode_flow(populated_ground_truth):
    async def _test():
        port = 8791
        server = ZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=populated_ground_truth)
        await server.start()

        try:
            edge_client = EdgeClientHarness(
                server_uri=f"ws://127.0.0.1:{port}",
                client_id="android_field_unit_01"
            )

            # 1. Connect & Handshake
            handshake = await edge_client.connect(project_id="vanilla_curing_cabinet")
            assert handshake["type"] == "SERVER_READY"
            assert handshake["status"] == "ARMED"

            # 2. Test Safe 5V DC Trigger & Response
            chunks = await edge_client.trigger_and_send_sensory_event(
                trigger_source=TriggerSource.BT_MEDIA_BUTTON,
                image_bytes=b"\xff\xd8\xff\xe0_mock_jpeg",
                audio_bytes=b"mock_audio",
                focus_locked=True,
                target_component_id="SSR_01",
                target_pin_id="3"
            )
            assert len(chunks) >= 1
            assert chunks[0].safety_flag == SafetyFlag.CLEAR

            # 3. Test 220V AC Interlock Warning (Pin 1)
            chunks_220v = await edge_client.trigger_and_send_sensory_event(
                trigger_source=TriggerSource.FOOT_SWITCH,
                image_bytes=b"\xff\xd8\xff\xe0_mock_jpeg",
                audio_bytes=b"mock_audio",
                focus_locked=True,
                target_component_id="SSR_01",
                target_pin_id="1"
            )
            assert len(chunks_220v) >= 1
            assert chunks_220v[0].safety_flag == SafetyFlag.INTERLOCK_WARNING
            assert "220V" in chunks_220v[0].text_transcript
            assert "breaker" in chunks_220v[0].text_transcript

            # 4. Test Blurry Frame Safety Halt
            chunks_blurry = await edge_client.trigger_and_send_sensory_event(
                trigger_source=TriggerSource.BT_MEDIA_BUTTON,
                image_bytes=b"blurry_bytes",
                audio_bytes=b"audio",
                focus_locked=False
            )
            assert len(chunks_blurry) == 1
            assert chunks_blurry[0].safety_flag == SafetyFlag.STOP_PROBE_REQUIRED

            await edge_client.close()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_e2e_pc_station_mode_flow(populated_ground_truth):
    async def _test():
        port = 8792
        server = ZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=populated_ground_truth)
        await server.start()

        try:
            station_client = StationIngestionClient(
                server_uri=f"ws://127.0.0.1:{port}",
                client_id="pc_cad_workstation"
            )

            # 1. Connect
            resp = await station_client.connect(project_id="vanilla_curing_cabinet")
            assert resp["type"] == "SERVER_READY"

            # 2. Ingest CAD screen frame for 5V DC line
            chunks = await station_client.send_screen_or_macro_frame(
                image_bytes=b"cad_screen_buffer",
                audio_query_bytes=b"where does pin 3 go",
                target_component_id="SSR_01",
                target_pin_id="3"
            )
            assert len(chunks) >= 1
            assert chunks[0].safety_flag == SafetyFlag.CLEAR

            await station_client.close()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_e2e_central_server_abort_and_document_flow(populated_ground_truth):
    async def _test():
        port = 8793
        server = CentralZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=populated_ground_truth)
        await server.start()

        try:
            ws = await websockets.connect(f"ws://127.0.0.1:{port}")

            # 1. Handshake
            hello = ClientHello(client_id="mobile_agent_01", client_mode=ClientMode.EDGE_FIELD)
            hello_data = hello.to_dict()
            hello_data["project_id"] = "vanilla_curing_cabinet"
            await ws.send(json.dumps(hello_data))
            raw_ready = await ws.recv()
            ready_resp = json.loads(raw_ready)
            assert ready_resp["type"] == "SERVER_READY"
            assert ready_resp["status"] == "ARMED"

            # 2. Attach Document ([INV-06] Session File Primacy)
            doc = AttachedDocumentPayload(
                file_name="engineering_spec.pdf",
                mime_type="application/pdf",
                size_bytes=8192,
                content_b64_or_text="PIN 12: High Precision Ground",
                priority_rank=1
            )
            doc_frame = doc.to_dict()
            doc_frame["type"] = "ATTACH_DOCUMENT"
            await ws.send(json.dumps(doc_frame))
            raw_doc_ack = await ws.recv()
            doc_ack = json.loads(raw_doc_ack)
            assert doc_ack["type"] == "DOCUMENT_ATTACHED"
            assert doc_ack["file_name"] == "engineering_spec.pdf"
            assert doc_ack["priority_rank"] == 1

            # 3. Set Personalization (Decoupled persona)
            personalization = UserPersonalizationConfig(
                persona_name="Engineering Specialist",
                tone_directive="Precise and brief"
            )
            p_frame = personalization.to_dict()
            p_frame["type"] = "SET_PERSONALIZATION"
            await ws.send(json.dumps(p_frame))
            raw_p_ack = await ws.recv()
            p_ack = json.loads(raw_p_ack)
            assert p_ack["type"] == "PERSONALIZATION_UPDATED"
            assert p_ack["persona_name"] == "Engineering Specialist"

            # 4. Abort Frame (<50ms intent match cancel)
            abort = AbortFrame(session_id="sess_mobile_agent_01", sequence_id=99, reason="LOCAL_INTENT_MATCHED")
            await ws.send(abort.to_json())
            raw_abort_ack = await ws.recv()
            abort_ack = json.loads(raw_abort_ack)
            assert abort_ack["type"] == "ABORT_ACK"
            assert abort_ack["status"] == "CANCELLED"
            assert abort_ack["sequence_id"] == 99

            await ws.close()
        finally:
            await server.stop()

    asyncio.run(_test())

