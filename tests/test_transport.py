import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.transport import WebSocketTransport

@pytest.fixture
def mock_callbacks():
    return {
        "on_message": AsyncMock(),
        "on_error": AsyncMock(),
        "on_close": AsyncMock(),
    }

@pytest.fixture
def transport(mock_callbacks):
    return WebSocketTransport(
        endpoint="ws://localhost:8080/stream",
        on_message=mock_callbacks["on_message"],
        on_error=mock_callbacks["on_error"],
        on_close=mock_callbacks["on_close"],
    )

@pytest.mark.asyncio
async def test_initial_state_disconnected(transport):
    assert not transport.is_connected

@pytest.mark.asyncio
async def test_connect_and_disconnect_lifecycle(transport):
    mock_ws = AsyncMock()
    mock_ws.close = AsyncMock()

    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = mock_ws

        connected = await transport.connect()
        assert connected
        assert transport.is_connected

        await transport.disconnect()
        assert not transport.is_connected
        mock_ws.close.assert_awaited_once()

@pytest.mark.asyncio
async def test_disconnect_is_idempotent(transport):
    await transport.disconnect()
    assert not transport.is_connected

@pytest.mark.asyncio
async def test_send_bytes_when_connected(transport):
    mock_ws = AsyncMock()
    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = mock_ws
        await transport.connect()

        payload = b"\x00\x01\x02\x03"
        success = await transport.send_bytes(payload)
        assert success
        mock_ws.send.assert_awaited_once_with(payload)

        await transport.disconnect()

@pytest.mark.asyncio
async def test_send_bytes_fails_when_disconnected(transport):
    payload = b"\x00\x01\x02\x03"
    success = await transport.send_bytes(payload)
    assert not success

@pytest.mark.asyncio
async def test_receive_loop_invokes_message_callback(transport, mock_callbacks):
    mock_ws = AsyncMock()
    test_payload = b"\xaa\xbb"
    
    # Simulate receiving one message then stopping
    async def mock_recv():
        if not hasattr(mock_recv, "called"):
            mock_recv.called = True
            return test_payload
        await asyncio.sleep(0.1)
        raise asyncio.CancelledError()

    mock_ws.recv = mock_recv

    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = mock_ws
        await transport.connect()

        # Yield control to let the receiver task process
        await asyncio.sleep(0.05)

        mock_callbacks["on_message"].assert_awaited_with(test_payload)
        await transport.disconnect()

@pytest.mark.asyncio
async def test_connect_failure_triggers_error_callback(transport, mock_callbacks):
    with patch("websockets.connect", side_effect=OSError("Connection refused")):
        connected = await transport.connect()
        assert not connected
        assert not transport.is_connected
        mock_callbacks["on_error"].assert_awaited_once()
