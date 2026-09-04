import asyncio
from enum import Enum, auto
from typing import Dict, Set

class State(Enum):
    IDLE = auto()
    CAPTURING = auto()
    STREAMING = auto()
    PLAYING = auto()
    ERROR = auto()

class Event(Enum):
    CAPTURE_START = auto()     # F20 Pressed
    CAPTURE_COMPLETE = auto()  # F20 Released
    PLAYBACK_START = auto()    # Cloud response receiving
    PLAYBACK_COMPLETE = auto() # Audio done
    INTERRUPT = auto()         # F20 pressed during playback
    FAULT = auto()             # Any unexpected network/hardware exception
    RESET = auto()             # Recovery back to IDLE

class InvalidStateTransitionError(Exception):
    """Raised when an invalid event is dispatched for the current state."""
    pass

class VoiceFSM:
    def __init__(self):
        self._state = State.IDLE
        self._lock = asyncio.Lock()
        
        # Explicit Valid Transition Matrix (Invariants)
        self._transitions: Dict[State, Dict[Event, State]] = {
            State.IDLE: {
                Event.CAPTURE_START: State.CAPTURING,
                Event.FAULT: State.ERROR
            },
            State.CAPTURING: {
                Event.CAPTURE_COMPLETE: State.STREAMING,
                Event.FAULT: State.ERROR
            },
            State.STREAMING: {
                Event.PLAYBACK_START: State.PLAYING,
                Event.FAULT: State.ERROR
            },
            State.PLAYING: {
                Event.PLAYBACK_COMPLETE: State.IDLE,
                Event.INTERRUPT: State.CAPTURING,
                Event.FAULT: State.ERROR
            },
            State.ERROR: {
                Event.RESET: State.IDLE
            }
        }

    @property
    def current_state(self) -> State:
        return self._state

    @property
    def state(self) -> State:
        return self._state

    @state.setter
    def state(self, val: State) -> None:
        self._state = val

    async def dispatch(self, event: Event) -> State:
        """Asynchronously dispatches an event under a mutex lock without blocking I/O."""
        async with self._lock:
            allowed_events = self._transitions.get(self._state, {})
            if event not in allowed_events:
                raise InvalidStateTransitionError(
                    f"Invalid transition: Cannot handle event {event.name} while in state {self._state.name}"
                )
            
            self._state = allowed_events[event]
            return self._state

# Aliases for compatibility
AppState = State
AppFSM = VoiceFSM
State.LISTENING = State.CAPTURING

