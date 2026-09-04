import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.fsm import VoiceFSM, State, Event
from src.interception_controller import F20ToggleController

@pytest.fixture
def fsm():
    return VoiceFSM()

@pytest.fixture
def controller(fsm):
    return F20ToggleController(fsm=fsm, debounce_seconds=0.05)

@pytest.mark.asyncio
async def test_initial_state_idle(controller, fsm):
    assert fsm.current_state == State.IDLE
    assert not controller.is_active

@pytest.mark.asyncio
async def test_f20_toggle_start_and_standby(controller, fsm):
    # 1st Press: Start / Active
    await controller.handle_f20_press()
    assert fsm.current_state == State.CAPTURING
    assert controller.is_active

    # Wait past debounce window
    await asyncio.sleep(0.06)

    # 2nd Press: Standby / Stop capture
    await controller.handle_f20_press()
    assert fsm.current_state == State.STREAMING
    assert not controller.is_active

@pytest.mark.asyncio
async def test_debounce_ignores_rapid_spikes(controller, fsm):
    # First press triggers
    await controller.handle_f20_press()
    assert fsm.current_state == State.CAPTURING

    # Immediate second press (< 50ms) must be ignored
    await controller.handle_f20_press()
    assert fsm.current_state == State.CAPTURING
    assert controller.is_active

@pytest.mark.asyncio
async def test_interruption_from_playing_state(controller, fsm):
    # Move FSM directly to PLAYING
    await fsm.dispatch(Event.CAPTURE_START)
    await fsm.dispatch(Event.CAPTURE_COMPLETE)
    await fsm.dispatch(Event.PLAYBACK_START)
    assert fsm.current_state == State.PLAYING

    # Pressing F20 while playing triggers INTERRUPT back to CAPTURING
    await asyncio.sleep(0.06)
    await controller.handle_f20_press()
    assert fsm.current_state == State.CAPTURING
    assert controller.is_active
