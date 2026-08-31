import pytest
from unittest.mock import patch, MagicMock
from src.audio_player import AudioPlayer, PlaybackState

@pytest.fixture(autouse=True)
def mock_pygame_env():
    mock_pygame = MagicMock()
    mock_pygame.mixer.get_init.return_value = True
    with patch.dict("sys.modules", {
        "pygame": mock_pygame,
        "pygame.mixer": mock_pygame.mixer,
        "pygame.mixer.music": mock_pygame.mixer.music
    }):
        yield mock_pygame

@pytest.fixture
def player():
    p = AudioPlayer(lazy_init=True)
    yield p
    p.stop()

def test_player_initial_state(player):
    assert player.state == PlaybackState.STOPPED
    assert player.is_stopped() is True
    assert player.is_playing() is False
    assert player.is_paused() is False

def test_player_play_pause_resume_stop(player):
    # 1. Play
    assert player.play(b"VALID_AUDIO_BYTES") is True
    assert player.state == PlaybackState.PLAYING
    assert player.is_playing() is True

    # 2. Pause
    assert player.pause() is True
    assert player.state == PlaybackState.PAUSED
    assert player.is_paused() is True

    # 3. Resume
    assert player.resume() is True
    assert player.state == PlaybackState.PLAYING
    assert player.is_playing() is True

    # 4. Stop
    assert player.stop() is True
    assert player.state == PlaybackState.STOPPED
    assert player.is_stopped() is True

def test_player_toggle_pause_resume(player):
    player.play(b"AUDIO")
    assert player.state == PlaybackState.PLAYING

    # Toggle to PAUSED
    state = player.toggle_pause_resume()
    assert state == PlaybackState.PAUSED
    assert player.is_paused() is True

    # Toggle to PLAYING
    state = player.toggle_pause_resume()
    assert state == PlaybackState.PLAYING
    assert player.is_playing() is True

def test_player_empty_audio(player):
    assert player.play(b"") is False
    assert player.state == PlaybackState.STOPPED

def test_player_unpause_and_get_state(player):
    player.play(b"AUDIO")
    assert player.get_state() == PlaybackState.PLAYING

    player.pause()
    assert player.get_state() == PlaybackState.PAUSED

    assert player.unpause() is True
    assert player.get_state() == PlaybackState.PLAYING

def test_player_queue_streaming_and_stop(player):
    # 1. Start queue playback with chunk 1
    assert player.start_queue_playback(b"CHUNK_1", is_last=False) is True
    assert player.is_playing() is True

    # 2. Enqueue chunk 2 & 3
    player.enqueue_chunk(b"CHUNK_2", is_last=False)
    player.enqueue_chunk(b"CHUNK_3", is_last=True)
    assert player._chunk_queue.qsize() == 2

    # 3. Stop -> must clear queue and stop playback
    player.stop()
    assert player.is_stopped() is True
    assert player._chunk_queue.empty() is True


def test_player_discard_chunk_when_stopped(player):
    player.stop()
    player.enqueue_chunk(b"NEW_CHUNK_AFTER_STOP", is_last=True)
    assert player.is_stopped() is True
    assert player._chunk_queue.empty() is True
