import pytest
import asyncio
from src.fsm import VoiceFSM, State, Event, InvalidStateTransitionError

@pytest.fixture
def fsm():
    return VoiceFSM()

@pytest.mark.asyncio
async def test_initial_state_is_idle(fsm):
    assert fsm.current_state == State.IDLE

@pytest.mark.asyncio
async def test_valid_forward_flow(fsm):
    # IDLE -> CAPTURING (F20 Pressed)
    await fsm.dispatch(Event.CAPTURE_START)
    assert fsm.current_state == State.CAPTURING

    # CAPTURING -> STREAMING (F20 Released)
    await fsm.dispatch(Event.CAPTURE_COMPLETE)
    assert fsm.current_state == State.STREAMING

    # STREAMING -> PLAYING (Cloud stream response arrived)
    await fsm.dispatch(Event.PLAYBACK_START)
    assert fsm.current_state == State.PLAYING

    # PLAYING -> IDLE (Playback completed)
    await fsm.dispatch(Event.PLAYBACK_COMPLETE)
    assert fsm.current_state == State.IDLE

@pytest.mark.asyncio
async def test_interruption_during_playback(fsm):
    # Transition to PLAYING
    await fsm.dispatch(Event.CAPTURE_START)
    await fsm.dispatch(Event.CAPTURE_COMPLETE)
    await fsm.dispatch(Event.PLAYBACK_START)
    assert fsm.current_state == State.PLAYING

    # Pressing F20 during PLAYING interrupts directly back to CAPTURING
    await fsm.dispatch(Event.INTERRUPT)
    assert fsm.current_state == State.CAPTURING

@pytest.mark.asyncio
async def test_error_transition_and_reset(fsm):
    # Error from STREAMING
    await fsm.dispatch(Event.CAPTURE_START)
    await fsm.dispatch(Event.CAPTURE_COMPLETE)
    assert fsm.current_state == State.STREAMING

    await fsm.dispatch(Event.FAULT)
    assert fsm.current_state == State.ERROR

    # Reset recovers to IDLE
    await fsm.dispatch(Event.RESET)
    assert fsm.current_state == State.IDLE

@pytest.mark.asyncio
async def test_invalid_transition_raises(fsm):
    # In IDLE, cannot trigger CAPTURE_COMPLETE or PLAYBACK_START
    with pytest.raises(InvalidStateTransitionError):
        await fsm.dispatch(Event.CAPTURE_COMPLETE)
    
    assert fsm.current_state == State.IDLE

@pytest.mark.asyncio
async def test_non_blocking_transition(fsm):
    # Transition must execute asynchronously and sub-millisecond
    start_time = asyncio.get_event_loop().time()
    await fsm.dispatch(Event.CAPTURE_START)
    elapsed = asyncio.get_event_loop().time() - start_time
    assert elapsed < 0.01  # Must complete well under 10ms
