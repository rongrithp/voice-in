import pytest
from unittest.mock import MagicMock
from src.fsm import VoiceFSM, State
from src.orchestrator import SystemOrchestrator
from src.transport import WebSocketTransport

def test_orchestrator_transport_callbacks_wiring():
    fsm = VoiceFSM()
    transport = WebSocketTransport()
    audio_player = MagicMock()

    orchestrator = SystemOrchestrator(
        fsm=fsm,
        transport=transport,
        audio_player=audio_player,
    )

    assert fsm.state == State.IDLE

    # Simulate incoming audio chunk via transport callback
    test_chunk = b"\x01\x02\x03\x04"
    transport._audio_callback(test_chunk)

    # Invariant: Audio chunk forwarded to player and FSM state is PLAYING
    audio_player.play_chunk.assert_called_once_with(test_chunk)
    assert fsm.state == State.PLAYING

    # Simulate interruption event via transport callback
    transport._interrupted_callback()

    # Invariant: Interrupted event immediately halts audio player
    audio_player.stop.assert_called_once()
