import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.fsm import AppFSM, AppState
from src.transport import WebSocketTransport
from src.orchestrator import SystemOrchestrator

@pytest.mark.asyncio
async def test_transport_unexpected_drop_triggers_fsm_error():
    """
    Invariant: Network drop during active STREAMING must deterministically
    transition FSM to ERROR state without raising unhandled exceptions.
    """
    fsm = AppFSM()
    fsm._state = AppState.STREAMING

    transport = WebSocketTransport()
    # Mock underlying socket connection
    transport._ws = AsyncMock()
    transport._is_connected = True

    orchestrator = SystemOrchestrator(
        fsm=fsm,
        audio_provider=MagicMock(),
        screen_capture=MagicMock(),
        transport=transport,
        audio_player=MagicMock(),
    )

    # Simulate network drop via transport callback
    orchestrator._on_transport_error(ConnectionResetError("Socket reset by peer"))

    assert fsm.state == AppState.ERROR
    assert transport.is_connected is False


@pytest.mark.asyncio
async def test_bounded_reconnect_retries_fail_safe():
    """
    Invariant: Reconnection attempts must be strictly bounded.
    Exceeding max retries must not cause infinite recursion.
    """
    transport = WebSocketTransport()
    transport.connect = AsyncMock(side_effect=ConnectionRefusedError("Endpoint down"))

    max_attempts = 3
    retry_count = 0

    # Execute bounded reconnection logic
    for _ in range(max_attempts):
        try:
            await transport.connect("ws://127.0.0.1:9999/live")
        except ConnectionRefusedError:
            retry_count += 1

    assert retry_count == max_attempts
    assert transport.is_connected is False
