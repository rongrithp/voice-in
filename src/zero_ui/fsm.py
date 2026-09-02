"""
Finite State Machines (FSM) for Zero-UI Real-Time Multimodal Personal Co-pilot.
Enforces session transitions, safety halts, and edge network fault recovery.
"""

from __future__ import annotations
import enum
import logging
from typing import Optional, Callable, Dict, Any, List

logger = logging.getLogger("zero_ui.fsm")


class ServerSessionState(str, enum.Enum):
    SESSION_INITIALIZED = "SESSION_INITIALIZED"
    STANDBY_ARMED = "STANDBY_ARMED"
    INGESTING_SENSORY = "INGESTING_SENSORY"
    SAFETY_GROUND_TRUTH_EVAL = "SAFETY_GROUND_TRUTH_EVAL"
    STREAMING_AUDIO_RESPONSE = "STREAMING_AUDIO_RESPONSE"
    SAFETY_HALT_PROBE = "SAFETY_HALT_PROBE"
    AWAITING_PHYSICAL_ACK = "AWAITING_PHYSICAL_ACK"
    ERROR_RECOVERY = "ERROR_RECOVERY"


class EdgeClientState(str, enum.Enum):
    BOOT_OFFLINE = "BOOT_OFFLINE"
    CONNECTING_CLOUD = "CONNECTING_CLOUD"
    CONNECTED_READY = "CONNECTED_READY"
    CAPTURING = "CAPTURING"
    BUFFERING_AND_UPLOADING = "BUFFERING_AND_UPLOADING"
    STREAMING_PLAYBACK = "STREAMING_PLAYBACK"
    OFFLINE_RETRY_QUEUE = "OFFLINE_RETRY_QUEUE"
    STANDBY_DORMANT = "STANDBY_DORMANT"


class ServerSessionFSM:
    """
    State Machine orchestrating central cloud backend personal co-pilot sessions.
    Guarantees that no response audio is streamed without passing Ground Truth and Safety evaluation.
    """

    ALLOWED_TRANSITIONS = {
        ServerSessionState.SESSION_INITIALIZED: [ServerSessionState.STANDBY_ARMED, ServerSessionState.ERROR_RECOVERY],
        ServerSessionState.STANDBY_ARMED: [ServerSessionState.INGESTING_SENSORY, ServerSessionState.ERROR_RECOVERY],
        ServerSessionState.INGESTING_SENSORY: [ServerSessionState.SAFETY_GROUND_TRUTH_EVAL, ServerSessionState.ERROR_RECOVERY],
        ServerSessionState.SAFETY_GROUND_TRUTH_EVAL: [
            ServerSessionState.STREAMING_AUDIO_RESPONSE,
            ServerSessionState.SAFETY_HALT_PROBE,
            ServerSessionState.ERROR_RECOVERY
        ],
        ServerSessionState.STREAMING_AUDIO_RESPONSE: [ServerSessionState.AWAITING_PHYSICAL_ACK, ServerSessionState.STANDBY_ARMED, ServerSessionState.ERROR_RECOVERY],
        ServerSessionState.SAFETY_HALT_PROBE: [ServerSessionState.AWAITING_PHYSICAL_ACK, ServerSessionState.STANDBY_ARMED, ServerSessionState.INGESTING_SENSORY, ServerSessionState.ERROR_RECOVERY],
        ServerSessionState.AWAITING_PHYSICAL_ACK: [ServerSessionState.STANDBY_ARMED, ServerSessionState.INGESTING_SENSORY, ServerSessionState.ERROR_RECOVERY],
        ServerSessionState.ERROR_RECOVERY: [ServerSessionState.STANDBY_ARMED, ServerSessionState.SESSION_INITIALIZED]
    }

    def __init__(self, session_id: str, on_state_change: Optional[Callable[[ServerSessionState, ServerSessionState], None]] = None):
        self.session_id = session_id
        self.state = ServerSessionState.SESSION_INITIALIZED
        self.on_state_change = on_state_change
        self.history: List[ServerSessionState] = [self.state]

    @property
    def current_state(self) -> ServerSessionState:
        return self.state

    def transition_to(self, new_state: ServerSessionState, reason: str = "") -> bool:
        if new_state not in self.ALLOWED_TRANSITIONS.get(self.state, []):
            logger.error(
                f"[FSM-{self.session_id}] Invalid transition from {self.state.value} -> {new_state.value} (Reason: {reason})"
            )
            return False

        old_state = self.state
        self.state = new_state
        self.history.append(new_state)
        logger.info(f"[FSM-{self.session_id}] State changed: {old_state.value} -> {new_state.value} ({reason})")

        if self.on_state_change:
            try:
                self.on_state_change(old_state, new_state)
            except Exception as e:
                logger.exception(f"Callback error during state transition: {e}")

        return True


import time


class TwoStageTimeoutFSM:
    """
    2-Stage Timeout Policy FSM Controller [INV-10 Revision]:
    - Stage 1: Turn silence timeout (turn_silence_timeout_sec, default 10.0s).
      Turn-taking: wait before sealing current speech turn while keeping connection active
      in a lightweight listening state.
    - Stage 2: Session idle timeout (session_idle_timeout_sec, default 60.0s).
      Dormant screensaver: idle duration before dropping client FSM to STANDBY_DORMANT.
    """

    def __init__(
        self,
        turn_silence_timeout_sec: float = 10.0,
        session_idle_timeout_sec: float = 60.0,
        on_turn_sealed: Optional[Callable[[], None]] = None,
        on_idle_dormant: Optional[Callable[[], None]] = None
    ):
        self.turn_silence_timeout_sec = turn_silence_timeout_sec
        self.session_idle_timeout_sec = session_idle_timeout_sec
        self.on_turn_sealed = on_turn_sealed
        self.on_idle_dormant = on_idle_dormant
        self.last_speech_time: float = time.time()
        self.last_activity_time: float = time.time()
        self.turn_sealed: bool = False
        self.is_dormant: bool = False

    def record_speech_activity(self, now: Optional[float] = None) -> None:
        """Called when user speech or audio chunk is actively streaming."""
        t = now if now is not None else time.time()
        self.last_speech_time = t
        self.last_activity_time = t
        self.turn_sealed = False
        self.is_dormant = False

    def record_interaction(self, now: Optional[float] = None) -> None:
        """Called when UI hotkey, touch, playback, or document event occurs."""
        t = now if now is not None else time.time()
        self.last_activity_time = t
        self.is_dormant = False

    def check_turn_silence(self, now: Optional[float] = None) -> bool:
        """Returns True if Stage 1 turn silence timeout exceeded (turn-taking seal)."""
        t = now if now is not None else time.time()
        return (t - self.last_speech_time) >= self.turn_silence_timeout_sec

    def check_session_idle(self, now: Optional[float] = None) -> bool:
        """Returns True if Stage 2 session idle screensaver timeout exceeded (drop to dormant)."""
        t = now if now is not None else time.time()
        return (t - self.last_activity_time) >= self.session_idle_timeout_sec

    def evaluate_timeouts(self, now: Optional[float] = None) -> Dict[str, bool]:
        """
        Evaluates 2-stage timeout policy:
        - Stage 1: Seals turn if silence >= turn_silence_timeout_sec (connection remains active in lightweight listening state).
        - Stage 2: Drops to dormant if idle >= session_idle_timeout_sec (dispatches teardown to STANDBY_DORMANT).
        """
        t = now if now is not None else time.time()
        sealed = False
        dormant = False

        if not self.turn_sealed and (t - self.last_speech_time) >= self.turn_silence_timeout_sec:
            self.turn_sealed = True
            sealed = True
            if self.on_turn_sealed:
                try:
                    self.on_turn_sealed()
                except Exception as e:
                    logger.exception(f"Callback error during turn seal: {e}")

        if not self.is_dormant and (t - self.last_activity_time) >= self.session_idle_timeout_sec:
            self.is_dormant = True
            dormant = True
            if self.on_idle_dormant:
                try:
                    self.on_idle_dormant()
                except Exception as e:
                    logger.exception(f"Callback error during idle dormant drop: {e}")

        return {"turn_sealed": sealed, "dropped_to_dormant": dormant}


class EdgeClientFSM:
    """
    State Machine orchestrating Android Edge / Mobile Personal Co-pilot client.
    Handles network dropouts, offline retry ring buffer, 2-stage timeouts, deep dormant standby, and hands-free trigger debounce.
    """

    ALLOWED_TRANSITIONS = {
        EdgeClientState.BOOT_OFFLINE: [EdgeClientState.CONNECTING_CLOUD],
        EdgeClientState.CONNECTING_CLOUD: [EdgeClientState.CONNECTED_READY, EdgeClientState.OFFLINE_RETRY_QUEUE, EdgeClientState.BOOT_OFFLINE],
        EdgeClientState.CONNECTED_READY: [EdgeClientState.CAPTURING, EdgeClientState.OFFLINE_RETRY_QUEUE, EdgeClientState.STANDBY_DORMANT],
        EdgeClientState.CAPTURING: [EdgeClientState.BUFFERING_AND_UPLOADING, EdgeClientState.OFFLINE_RETRY_QUEUE, EdgeClientState.CONNECTED_READY],
        EdgeClientState.BUFFERING_AND_UPLOADING: [EdgeClientState.STREAMING_PLAYBACK, EdgeClientState.OFFLINE_RETRY_QUEUE, EdgeClientState.CONNECTED_READY],
        EdgeClientState.STREAMING_PLAYBACK: [EdgeClientState.CONNECTED_READY, EdgeClientState.OFFLINE_RETRY_QUEUE],
        EdgeClientState.OFFLINE_RETRY_QUEUE: [EdgeClientState.CONNECTING_CLOUD, EdgeClientState.CONNECTED_READY, EdgeClientState.BOOT_OFFLINE],
        EdgeClientState.STANDBY_DORMANT: [EdgeClientState.CONNECTED_READY, EdgeClientState.CAPTURING, EdgeClientState.CONNECTING_CLOUD, EdgeClientState.BOOT_OFFLINE]
    }

    def __init__(
        self,
        client_id: str,
        on_state_change: Optional[Callable[[EdgeClientState, EdgeClientState], None]] = None,
        turn_silence_timeout_sec: float = 10.0,
        session_idle_timeout_sec: float = 60.0
    ):
        self.client_id = client_id
        self.state = EdgeClientState.BOOT_OFFLINE
        self.on_state_change = on_state_change
        self.history: List[EdgeClientState] = [self.state]
        self.turn_silence_timeout_sec = turn_silence_timeout_sec
        self.session_idle_timeout_sec = session_idle_timeout_sec
        self.timeout_policy = TwoStageTimeoutFSM(
            turn_silence_timeout_sec=turn_silence_timeout_sec,
            session_idle_timeout_sec=session_idle_timeout_sec,
            on_idle_dormant=self._on_idle_dormant_trigger
        )

    def _on_idle_dormant_trigger(self) -> None:
        if self.state != EdgeClientState.STANDBY_DORMANT and self.state in self.ALLOWED_TRANSITIONS:
            if EdgeClientState.STANDBY_DORMANT in self.ALLOWED_TRANSITIONS[self.state]:
                self.transition_to(EdgeClientState.STANDBY_DORMANT, "Session idle timeout (Stage 2: 60.0s)")

    @property
    def current_state(self) -> EdgeClientState:
        return self.state

    def record_speech_activity(self, now: Optional[float] = None) -> None:
        self.timeout_policy.record_speech_activity(now)

    def record_interaction(self, now: Optional[float] = None) -> None:
        self.timeout_policy.record_interaction(now)

    def evaluate_timeouts(self, now: Optional[float] = None) -> Dict[str, bool]:
        return self.timeout_policy.evaluate_timeouts(now)

    def transition_to(self, new_state: EdgeClientState, reason: str = "") -> bool:
        if new_state not in self.ALLOWED_TRANSITIONS.get(self.state, []):
            logger.error(
                f"[EdgeFSM-{self.client_id}] Invalid transition from {self.state.value} -> {new_state.value} (Reason: {reason})"
            )
            return False

        old_state = self.state
        self.state = new_state
        self.history.append(new_state)
        self.timeout_policy.record_interaction()
        logger.info(f"[EdgeFSM-{self.client_id}] State changed: {old_state.value} -> {new_state.value} ({reason})")

        if self.on_state_change:
            try:
                self.on_state_change(old_state, new_state)
            except Exception as e:
                logger.exception(f"Callback error during edge state transition: {e}")

        return True
