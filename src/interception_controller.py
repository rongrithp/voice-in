import asyncio
import time
from typing import Optional
from src.fsm import VoiceFSM, State, Event

class F20ToggleController:
    """
    Manages global F20 toggle lifecycle, debouncing, and state synchronization.
    """
    def __init__(self, fsm: VoiceFSM, debounce_seconds: float = 0.15):
        self._fsm = fsm
        self._debounce_seconds = debounce_seconds
        self._last_press_time: float = 0.0
        self._is_active: bool = False
        self._lock = asyncio.Lock()

    @property
    def is_active(self) -> bool:
        return self._is_active

    async def handle_f20_press(self) -> None:
        """Processes incoming F20 keystroke with debounce and FSM synchronization."""
        async with self._lock:
            now = time.monotonic()
            if (now - self._last_press_time) < self._debounce_seconds:
                return  # Discard hardware bounce
            
            self._last_press_time = now
            current = self._fsm.current_state

            if current == State.IDLE:
                await self._fsm.dispatch(Event.CAPTURE_START)
                self._is_active = True
            elif current == State.CAPTURING:
                await self._fsm.dispatch(Event.CAPTURE_COMPLETE)
                self._is_active = False
            elif current == State.PLAYING:
                await self._fsm.dispatch(Event.INTERRUPT)
                self._is_active = True
            elif current == State.STREAMING:
                # If pressed while awaiting cloud response, trigger interruption
                await self._fsm.dispatch(Event.FAULT)
                await self._fsm.dispatch(Event.RESET)
                self._is_active = False
