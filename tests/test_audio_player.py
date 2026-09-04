import pytest
import asyncio
from unittest.mock import MagicMock, patch
from src.audio_player import AudioPlayer

@pytest.fixture
def player():
    return AudioPlayer(samplerate=24000, channels=1)

@pytest.mark.asyncio
async def test_initial_state_stopped(player):
    assert not player.is_playing
    assert player.queue_size == 0

@pytest.mark.asyncio
async def test_play_chunk_queues_data(player):
    await player.play_chunk(b"\x01\x02\x03\x04")
    assert player.queue_size == 1

@pytest.mark.asyncio
async def test_instant_stop_and_flush(player):
    # Queue multiple chunks
    for _ in range(5):
        await player.play_chunk(b"\x00" * 512)
    
    assert player.queue_size == 5

    # Simulate running stream
    mock_stream = MagicMock()
    player._stream = mock_stream
    player._is_playing = True

    # Immediate flush
    await player.stop()

    assert player.queue_size == 0
    assert not player.is_playing
    mock_stream.stop.assert_called_once()
    mock_stream.close.assert_called_once()

@pytest.mark.asyncio
async def test_stop_is_idempotent(player):
    await player.stop()
    assert not player.is_playing
    assert player.queue_size == 0
