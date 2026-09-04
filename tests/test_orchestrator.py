import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.fsm import VoiceFSM, State, Event
from src.orchestrator import SystemOrchestrator

@pytest.fixture
def mock_components():
    return {
        "fsm": VoiceFSM(),
        "keyboard_hook": MagicMock(),
        "audio_provider": MagicMock(),
        "audio_buffer": AsyncMock(),
        "screen_capture": AsyncMock(),
        "transport": AsyncMock(),
        "audio_player": AsyncMock(),
    }

@pytest.fixture
def orchestrator(mock_components):
    return SystemOrchestrator(
        fsm=mock_components["fsm"],
        keyboard_hook=mock_components["keyboard_hook"],
        audio_provider=mock_components["audio_provider"],
        audio_buffer=mock_components["audio_buffer"],
        screen_capture=mock_components["screen_capture"],
        transport=mock_components["transport"],
        audio_player=mock_components["audio_player"],
    )

@pytest.mark.asyncio
async def test_startup_and_shutdown_lifecycle(orchestrator, mock_components):
    mock_components["transport"].connect = AsyncMock(return_value=True)
    
    await orchestrator.start()
    assert orchestrator.is_running
    mock_components["keyboard_hook"].start.assert_called_once()
    mock_components["transport"].connect.assert_awaited_once()

    await orchestrator.shutdown()
    assert not orchestrator.is_running
    mock_components["keyboard_hook"].stop.assert_called_once()
    mock_components["audio_provider"].stop.assert_called_once()
    mock_components["audio_player"].stop.assert_awaited_once()
    mock_components["transport"].disconnect.assert_awaited_once()

@pytest.mark.asyncio
async def test_transition_to_capturing_starts_audio_and_screen(orchestrator, mock_components):
    mock_components["screen_capture"].capture.return_value = b"FAKE_SCREEN_JPEG"
    
    # Trigger state change to CAPTURING
    await orchestrator._on_state_changed(State.CAPTURING)

    mock_components["audio_provider"].start.assert_called_once()
    mock_components["audio_player"].stop.assert_awaited_once()  # Ensure speaker muted
    mock_components["screen_capture"].capture.assert_awaited_once()
    assert orchestrator.last_screen_payload == b"FAKE_SCREEN_JPEG"

@pytest.mark.asyncio
async def test_transition_to_streaming_drains_audio_to_transport(orchestrator, mock_components):
    mock_components["audio_buffer"].drain_all.return_value = [b"chunk1", b"chunk2"]
    orchestrator._last_screen_payload = b"FAKE_SCREEN"
    
    await orchestrator._on_state_changed(State.STREAMING)

    mock_components["audio_provider"].stop.assert_called_once()
    mock_components["audio_buffer"].drain_all.assert_awaited_once()
    
    # Verify screen and audio sent over transport
    assert mock_components["transport"].send_bytes.await_count == 3  # 1 screen + 2 audio chunks

@pytest.mark.asyncio
async def test_incoming_transport_message_routes_to_player(orchestrator, mock_components):
    audio_response = b"\x05\x06\x07"
    await orchestrator._handle_transport_message(audio_response)
    
    mock_components["audio_player"].play_chunk.assert_awaited_once_with(audio_response)
