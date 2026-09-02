"""
Stress, Performance, and Latency Profiling Tests for Zero-UI Real-Time Multimodal Personal Co-pilot (Phase 4).
Benchmarking:
- Round-trip Ingestion to First Audio Chunk Latency (<350ms SLA) [INV-03]
- Hardware trigger debounce under rapid hammering conditions
- High-frequency session turns verifying Ephemeral RAM leak-free lifecycle [INV-04]
- Network drop & WebSocket reconnection resilience without zombie tasks
"""

import asyncio
import json
import time
import pytest
import websockets

from src.zero_ui.contracts import (
    TriggerSource,
    SensorPayload,
    ImagePayload,
    AudioPayload,
    TelemetryPayload,
    ServerAudioStreamChunk,
    ClientHello,
    ClientMode,
    SafetyFlag
)
from src.zero_ui.server import CentralZeroUIServer
from src.zero_ui.orchestrator import SessionOrchestrator
from src.zero_ui.ground_truth import GroundTruthEngine
from src.zero_ui.mock_edge_client import EdgeClientHarness, TriggerDebouncedError
from src.zero_ui.fsm import EdgeClientState


def test_roundtrip_latency_first_audio_chunk():
    """
    Benchmark round-trip ingestion to first audio chunk latency against < 350ms SLA [INV-03].
    """
    async def _test():
        port = 8795
        engine = GroundTruthEngine(":memory:")
        server = CentralZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=engine)
        await server.start()

        try:
            client = EdgeClientHarness(
                server_uri=f"ws://127.0.0.1:{port}",
                client_id="latency_test_client",
                debounce_interval_ms=0
            )
            await client.connect(project_id="perf_benchmark_project")

            first_chunk_arrival_ns = 0
            start_ns = time.time_ns()

            def on_chunk(chunk: ServerAudioStreamChunk):
                nonlocal first_chunk_arrival_ns
                if first_chunk_arrival_ns == 0:
                    first_chunk_arrival_ns = time.time_ns()

            client.on_audio_received = on_chunk

            chunks = await client.trigger_and_send_sensory_event(
                trigger_source=TriggerSource.BT_MEDIA_BUTTON,
                image_bytes=b"benchmark_frame",
                audio_bytes=b"benchmark_query",
                focus_locked=True
            )

            assert len(chunks) >= 1
            assert first_chunk_arrival_ns > 0

            latency_ms = (first_chunk_arrival_ns - start_ns) / 1_000_000
            logger_msg = f"Round-trip latency to first audio chunk: {latency_ms:.2f}ms (SLA Target: < 350ms)"
            print(logger_msg)

            # Assert SLA target < 350ms
            assert latency_ms < 350.0, f"Latency SLA violation: {latency_ms:.2f}ms >= 350ms"

            await client.close()
        finally:
            await server.stop()

    asyncio.run(_test())


def test_hardware_trigger_debounce_rapid_hammering():
    """
    Simulates rapid physical button hammering (50 rapid triggers) to verify debounce filtering.
    """
    client = EdgeClientHarness(debounce_interval_ms=150)

    accepted_count = 0
    debounced_count = 0

    # Hammer button 50 times in rapid succession
    for _ in range(50):
        if client.check_trigger_debounce(TriggerSource.BT_MEDIA_BUTTON):
            accepted_count += 1
        else:
            debounced_count += 1

    # Exactly 1 trigger accepted, remaining 49 debounced
    assert accepted_count == 1
    assert debounced_count == 49

    # Wait out debounce window
    time.sleep(0.16)

    # Next trigger after window must be accepted
    assert client.check_trigger_debounce(TriggerSource.BT_MEDIA_BUTTON) is True


def test_high_frequency_session_turns_ephemeral_ram_leak_free():
    """
    Executes 30 high-frequency sensory payload turns to verify Ephemeral RAM is 100% leak-free [INV-04].
    """
    async def _test():
        engine = GroundTruthEngine(":memory:")
        orchestrator = SessionOrchestrator(
            session_id="sess_stress_ram",
            project_id="stress_test_project",
            ground_truth_engine=engine
        )
        orchestrator.handle_client_hello(ClientHello(client_id="stress_edge_01", client_mode=ClientMode.EDGE_FIELD))

        for turn in range(1, 31):
            payload = SensorPayload(
                session_id="sess_stress_ram",
                sequence_id=turn,
                image=ImagePayload(data=f"high_res_frame_bytes_turn_{turn}" * 100),
                audio_query=AudioPayload(data=f"pcm_audio_query_turn_{turn}" * 50),
                telemetry=TelemetryPayload(focus_locked=True)
            )

            # Before processing turn, buffer must be clean
            assert orchestrator.ephemeral_buffer.current_image is None
            assert orchestrator.ephemeral_buffer.current_audio is None

            chunks = []
            async for chunk in orchestrator.process_sensor_payload(payload):
                chunks.append(chunk)

            # After processing turn, Ephemeral RAM MUST be purged [INV-04]
            assert orchestrator.ephemeral_buffer.current_image is None
            assert orchestrator.ephemeral_buffer.current_audio is None
            assert orchestrator.ephemeral_buffer.current_telemetry is None
            assert len(chunks) >= 1

        # Verify Tier 2 dialogue history recorded all 30 turns
        assert len(orchestrator.dialogue_history) == 30

    asyncio.run(_test())


def test_network_drop_and_reconnection_zombie_task_resilience():
    """
    Simulates abrupt socket disconnects mid-flight and verifies clean server recovery & reconnection.
    """
    async def _test():
        port = 8796
        engine = GroundTruthEngine(":memory:")
        server = CentralZeroUIServer(host="127.0.0.1", port=port, ground_truth_engine=engine)
        await server.start()

        try:
            # 1. First Connection
            client1 = EdgeClientHarness(
                server_uri=f"ws://127.0.0.1:{port}",
                client_id="reconnect_resilience_unit",
                debounce_interval_ms=0
            )
            await client1.connect(project_id="resilience_project")

            assert len(server.connected_clients) == 1
            assert len(server.active_sessions) == 1

            # 2. Abrupt Disconnect (Simulate drop)
            await client1.close()

            # Small pause to allow WebSocket close event to propagate
            await asyncio.sleep(0.05)

            # Server connection list should clean up automatically
            assert "reconnect_resilience_unit" not in server.connected_clients

            # 3. Client Reconnects via connect_with_retry
            client2 = EdgeClientHarness(
                server_uri=f"ws://127.0.0.1:{port}",
                client_id="reconnect_resilience_unit",
                debounce_interval_ms=0
            )
            resp = await client2.connect_with_retry(project_id="resilience_project", max_retries=2)
            assert resp["type"] == "SERVER_READY"
            assert client2.fsm.state == EdgeClientState.CONNECTED_READY

            # New payload turn succeeds
            chunks = await client2.trigger_and_send_sensory_event(
                trigger_source=TriggerSource.BT_MEDIA_BUTTON,
                image_bytes=b"reconnected_frame",
                audio_bytes=b"reconnected_audio"
            )
            assert len(chunks) >= 1

            await client2.close()
        finally:
            await server.stop()

    asyncio.run(_test())
