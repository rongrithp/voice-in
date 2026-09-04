import time
import pytest
from unittest.mock import MagicMock
from src.audio_player import AudioPlayer
from src.orchestrator import SystemOrchestrator
from src.fsm import AppFSM, AppState

def test_audio_player_instant_flush_latency():
    """
    Invariant: audio_player.stop() must clear all pending frames
    and return within 20 milliseconds.
    """
    player = AudioPlayer()
    # Mocking stream to avoid hardware audio loopback delays in CI
    player._stream = MagicMock()
    player._is_playing = True

    # Pre-populate queue with multiple audio chunks (simulate heavy output buffer)
    dummy_chunk = b"\x00\x01" * 1024
    for _ in range(50):
        player.play_chunk(dummy_chunk)

    assert player.get_queued_chunk_count() > 0 or not player._queue.empty()

    # Measure stop latency
    t_start = time.perf_counter()
    player.stop()
    t_elapsed = (time.perf_counter() - t_start) * 1000.0  # ms

    # Latency invariant: < 20ms
    assert t_elapsed < 20.0, f"Stop latency {t_elapsed:.2f}ms exceeded 20ms threshold"

    # Residual frames invariant: queue must be completely drained
    assert player._queue.empty(), "AudioPlayer queue contains residual chunks after stop"
    assert player.is_playing is False


def test_orchestrator_interruption_state_and_flush_invariant():
    """
    Invariant: When FSM interrupts PLAYING -> CAPTURING,
    Orchestrator must immediately signal AudioPlayer to flush
    and ensure zero residual frames.
    """
    fsm = AppFSM()
    player = AudioPlayer()
    player._stream = MagicMock()
    transport = MagicMock()
    audio_provider = MagicMock()
    screen_capture = MagicMock()

    orchestrator = SystemOrchestrator(
        fsm=fsm,
        audio_provider=audio_provider,
        screen_capture=screen_capture,
        transport=transport,
        audio_player=player,
    )

    # Set up active PLAYING state with pending frames
    fsm._state = AppState.PLAYING
    dummy_chunk = b"\xAA\x55" * 512
    for _ in range(20):
        player.play_chunk(dummy_chunk)

    # Execute interruption transition (e.g. user taps F20 during assistant speech)
    t_start = time.perf_counter()
    orchestrator.handle_interruption()
    t_elapsed = (time.perf_counter() - t_start) * 1000.0

    # Verification of Invariants
    assert t_elapsed < 20.0, f"Interruption handling {t_elapsed:.2f}ms exceeded 20ms"
    assert fsm.state == AppState.CAPTURING
    assert player._queue.empty()
    assert player.is_playing is False
