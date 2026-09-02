"""
Edge Client Harness for Zero-UI Real-Time Multimodal Personal Co-pilot.
Simulates Android Headless Client (Field Mode) with Bluetooth & Floating Shutter trigger debounce,
Camera snapshot serialization, permission-gated telemetry [INV-05], and cloud auto-reconnect backoff.
"""

from __future__ import annotations
import asyncio
import base64
import json
import logging
import time
from typing import Optional, List, Callable, Dict, Any
import websockets

from src.zero_ui.contracts import (
    ClientHello,
    ClientMode,
    ClientCapabilities,
    CaptureTriggerEvent,
    TriggerSource,
    SensorPayload,
    ImagePayload,
    AudioPayload,
    TelemetryPayload,
    ServerAudioStreamChunk,
    SafetyFlag,
    LockScreenIndicatorStatus,
    AndroidOngoingNotificationState,
    GpsCoordinates,
    MotionTelemetry,
    DeviceHealthTelemetry,
    WakeWordConfig,
    EndStreamFrame,
    QuickDropPayload,
    ThumbnailOverlayState,
    AudioConfig
)
from src.zero_ui.fsm import EdgeClientFSM, EdgeClientState
from src.zero_ui.media import compress_image_frame, TimeStretchAudioSink, DynamicRMSNoiseGate

logger = logging.getLogger("zero_ui.edge_client")


class TriggerDebouncedError(Exception):
    """Raised when hardware trigger occurs faster than the configured debounce interval."""
    pass


class EdgeClientHarness:
    """
    Simulates Android Hardware Client (Headless Foreground Service).
    Connects to Central Cloud Backend over secure WebSockets (wss://).
    Manages lock-screen HUD indicator, trigger debouncing, camera serialization,
    dynamic noise floor tracking, 2-stage timeout policy (turn-silence vs dormant),
    transient floating image thumbnail overlay [INV-12], and reconnect backoff.
    """

    def __init__(
        self,
        server_uri: str = "ws://127.0.0.1:8765",
        client_id: str = "android_edge_01",
        on_audio_received: Optional[Callable[[ServerAudioStreamChunk], None]] = None,
        debounce_interval_ms: int = 0,
        playback_speed: float = 1.0,
        wake_word: Optional[WakeWordConfig] = None,
        rms_silence_timeout_sec: Optional[float] = None,
        thumbnail_dismiss_timeout_sec: Optional[float] = None,
        turn_silence_timeout_sec: Optional[float] = None,
        session_idle_timeout_sec: Optional[float] = None,
        audio_config: Optional[AudioConfig] = None,
        config: Optional[UserRuntimeConfig] = None
    ):
        self.server_uri = server_uri
        self.client_id = client_id
        self.config = config
        self.audio_config = audio_config or (config.audio if config else AudioConfig())
        self.turn_silence_timeout_sec = (
            turn_silence_timeout_sec
            if turn_silence_timeout_sec is not None
            else self.audio_config.turn_silence_timeout_sec
        )
        self.session_idle_timeout_sec = (
            session_idle_timeout_sec
            if session_idle_timeout_sec is not None
            else self.audio_config.session_idle_timeout_sec
        )
        self.fsm = EdgeClientFSM(
            client_id,
            turn_silence_timeout_sec=self.turn_silence_timeout_sec,
            session_idle_timeout_sec=self.session_idle_timeout_sec
        )
        self.on_audio_received = on_audio_received
        self.debounce_interval_ms = debounce_interval_ms
        self.wake_word = wake_word or (config.wake_word if config else WakeWordConfig())
        self.rms_silence_timeout_sec = (
            rms_silence_timeout_sec
            if rms_silence_timeout_sec is not None
            else (config.rms_silence_timeout_sec if config else 5.0)
        )
        self.thumbnail_dismiss_timeout_sec = (
            thumbnail_dismiss_timeout_sec
            if thumbnail_dismiss_timeout_sec is not None
            else (config.thumbnail_dismiss_timeout_sec if config else 4.0)
        )
        self.thumbnail_state: ThumbnailOverlayState = ThumbnailOverlayState.HIDDEN
        self.active_thumbnail_bytes: Optional[bytes] = None
        self.thumbnail_display_timestamp: Optional[float] = None
        self.last_speech_time: float = time.time()
        self.last_activity_time: float = time.time()
        self.is_turn_sealed: bool = False
        self.is_listening_active: bool = False
        self.audio_sink = TimeStretchAudioSink(sample_rate=24000, playback_speed=playback_speed)
        self.dynamic_noise_gate = DynamicRMSNoiseGate(
            silence_teardown_sec=self.rms_silence_timeout_sec,
            on_silence_teardown=self._on_silence_teardown_sync
        )
        self.last_trigger_timestamps: Dict[TriggerSource, int] = {}
        self.permissions: Dict[str, bool] = {
            "gps": False,
            "motion": False,
            "device_health": False
        }
        self.lock_screen_status: LockScreenIndicatorStatus = LockScreenIndicatorStatus.READY
        self.notification_state: AndroidOngoingNotificationState = AndroidOngoingNotificationState.READY
        self.is_muted: bool = False
        self.last_tap_timestamps: Dict[str, float] = {}
        self.double_tap_window_sec: float = 0.5
        self.haptic_feedback_events: List[str] = []
        self._teardown_lock = asyncio.Lock()
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.received_chunks: List[ServerAudioStreamChunk] = []

    def _on_silence_teardown_sync(self) -> None:
        """Invoked when dynamic noise floor detects silence > 2.0s."""
        logger.info(f"[EdgeClient:{self.client_id}] Dynamic RMS silence teardown triggered (>2.0s).")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.teardown_stream_to_dormant("RMS_SILENCE_TIMEOUT"))
        except RuntimeError:
            if self.fsm.current_state != EdgeClientState.STANDBY_DORMANT:
                self.fsm.transition_to(EdgeClientState.STANDBY_DORMANT, "Silence teardown (sync fallback)")

    async def teardown_stream_to_dormant(self, reason: str = "RMS_SILENCE_TIMEOUT") -> Optional[Dict[str, Any]]:
        """Send EndStreamFrame, flush buffers, and drop client FSM back to STANDBY_DORMANT."""
        async with self._teardown_lock:
            if self.fsm.current_state == EdgeClientState.STANDBY_DORMANT:
                return {"type": "STREAM_TEARDOWN_ACK", "session_id": f"sess_{self.client_id}", "status": "STANDBY_DORMANT"}

            ack = None
            if self.ws:
                frame = EndStreamFrame(session_id=f"sess_{self.client_id}", reason=reason)
                await self.ws.send(frame.to_json())
                raw_ack = await self.ws.recv()
                ack = json.loads(raw_ack)
            self.audio_sink.halt()
            if self.fsm.current_state != EdgeClientState.STANDBY_DORMANT:
                self.fsm.transition_to(EdgeClientState.STANDBY_DORMANT, f"Silence teardown: {reason}")
            return ack

    def is_wake_word_triggered(self, text: str) -> bool:
        """Dynamic wake-word verification (prohibits hardcoded string matching)."""
        return self.wake_word.matches(text)

    async def handle_action_send(self, shared_text_or_url: str) -> Dict[str, Any]:
        """
        Android ACTION_SEND Intent Handler:
        Ingests shared text/URLs straight into active WebSocket session buffer without retaining UI.
        """
        if not self.ws:
            raise RuntimeError("Edge client not connected.")
        payload = QuickDropPayload(content=shared_text_or_url, source="ANDROID_ACTION_SEND")
        await self.ws.send(payload.to_json())
        raw_ack = await self.ws.recv()
        return json.loads(raw_ack)

    def set_permissions(self, permissions: Dict[str, bool]) -> None:
        """Update granular hardware sensor permissions [INV-05]."""
        self.permissions.update(permissions)
        logger.info(f"[EdgeClient:{self.client_id}] Permissions updated: {self.permissions}")

    # --- Pocket-Safe Floating Buttons (Lock Screen & Ambient Overlay) ---
    def check_double_tap(self, action_name: str, now: Optional[float] = None) -> bool:
        """
        Pocket-Safe Guard:
        Requires two taps within double_tap_window_sec (default 0.5s) to activate.
        Single taps do not trigger action, preventing pocket misfires.
        """
        t = now if now is not None else time.time()
        last_t = self.last_tap_timestamps.get(action_name, 0.0)
        elapsed = t - last_t

        if last_t > 0 and elapsed <= self.double_tap_window_sec:
            # Successful double-tap! Reset timestamp
            self.last_tap_timestamps[action_name] = 0.0
            self.trigger_haptic_feedback(f"DOUBLE_TAP_{action_name.upper()}_CONFIRMED")
            logger.info(f"[EdgeClient:{self.client_id}] Pocket-safe double-tap confirmed for '{action_name}' ({elapsed:.2f}s).")
            return True
        else:
            self.last_tap_timestamps[action_name] = t
            logger.debug(f"[EdgeClient:{self.client_id}] First tap registered for '{action_name}'. Awaiting second tap within {self.double_tap_window_sec}s.")
            return False

    def trigger_haptic_feedback(self, pattern: str = "CLICK") -> None:
        """Emits haptic vibration pattern upon successful pocket-safe trigger."""
        self.haptic_feedback_events.append(pattern)
        logger.debug(f"[EdgeClient:{self.client_id}] Haptic vibration triggered: {pattern}")

    def double_tap_toggle_mute(self, now: Optional[float] = None) -> bool:
        """
        Pocket-Safe Floating Button 1: Toggle Power / Mute
        Enables or disables background listening.
        Requires double-tap to activate.
        Returns True if toggle executed, False if waiting for second tap.
        """
        if not self.check_double_tap("toggle_mute", now=now):
            return False

        self.is_muted = not self.is_muted
        if self.is_muted:
            self.lock_screen_status = LockScreenIndicatorStatus.MUTED
            self.notification_state = AndroidOngoingNotificationState.MUTED
            logger.info(f"[EdgeClient:{self.client_id}] Background listening MUTED.")
        else:
            self.lock_screen_status = LockScreenIndicatorStatus.READY
            self.notification_state = AndroidOngoingNotificationState.READY
            logger.info(f"[EdgeClient:{self.client_id}] Background listening UNMUTED (READY).")
        return True

    async def double_tap_quick_snap(
        self,
        image_bytes: bytes = b"quick_snap_jpeg_data",
        audio_query_bytes: bytes = b"",
        now: Optional[float] = None
    ) -> Optional[List[ServerAudioStreamChunk]]:
        """
        Pocket-Safe Floating Button 2: Quick Snap
        Triggers camera capture, dispatches image over WebSocket,
        and displays 4.0s transient thumbnail overlay [INV-12].
        Requires double-tap to activate.
        Returns response chunks if triggered, None if waiting for second tap.
        """
        if not self.check_double_tap("quick_snap", now=now):
            return None

        logger.info(f"[EdgeClient:{self.client_id}] Quick Snap activated via pocket-safe double-tap.")
        # Trigger sensory event with FLOATING_SHUTTER_BUTTON
        return await self.trigger_and_send_sensory_event(
            trigger_source=TriggerSource.FLOATING_SHUTTER_BUTTON,
            image_bytes=image_bytes,
            audio_bytes=audio_query_bytes
        )

    # --- Android Quick Settings Tile (Screen Capture) ---
    async def trigger_quick_settings_screen_capture(
        self,
        screen_frame_bytes: bytes = b"android_screen_capture_jpeg",
        audio_query_bytes: bytes = b""
    ) -> List[ServerAudioStreamChunk]:
        """
        Triggered via Android Quick Settings Tile:
        Captures Android device display, compresses frame, dispatches to cloud session.
        """
        logger.info(f"[EdgeClient:{self.client_id}] Screen capture triggered via Android Quick Settings Tile.")
        return await self.trigger_and_send_sensory_event(
            trigger_source=TriggerSource.FLOATING_SHUTTER_BUTTON,
            image_bytes=screen_frame_bytes,
            audio_bytes=audio_query_bytes
        )

    # --- Android Transient Image Thumbnail Overlay [INV-12] ---
    def render_thumbnail(self, image_bytes: bytes, now: Optional[float] = None) -> None:
        """Render transient floating thumbnail overlay preview when image frame is ingested [INV-12]."""
        self.active_thumbnail_bytes = image_bytes
        self.thumbnail_state = ThumbnailOverlayState.THUMBNAIL_VISIBLE
        self.thumbnail_display_timestamp = now if now is not None else time.time()
        logger.info(
            f"[EdgeClient:{self.client_id}] Transient image thumbnail rendered "
            f"({len(image_bytes)} bytes, auto-dismiss: {self.thumbnail_dismiss_timeout_sec}s)."
        )

    def check_thumbnail_timeout(self, now: Optional[float] = None) -> bool:
        """
        If untouched for thumbnail_dismiss_timeout_sec (default 4.0s), auto-dismiss/fade the thumbnail into background.
        Returns True if dismissed, False otherwise.
        """
        if self.thumbnail_state != ThumbnailOverlayState.THUMBNAIL_VISIBLE:
            return False

        t = now if now is not None else time.time()
        if self.thumbnail_display_timestamp is not None and (t - self.thumbnail_display_timestamp) >= self.thumbnail_dismiss_timeout_sec:
            self.dismiss_thumbnail(reason="TIMEOUT_AUTO_DISMISS")
            return True
        return False

    def tap_thumbnail(self) -> bool:
        """
        If tapped before dismissal, expand into full-size preview overlay [INV-12].
        Returns True if successfully expanded, False if thumbnail was not visible.
        """
        if self.thumbnail_state == ThumbnailOverlayState.THUMBNAIL_VISIBLE:
            self.thumbnail_state = ThumbnailOverlayState.EXPANDED
            logger.info(f"[EdgeClient:{self.client_id}] Thumbnail tapped -> expanded to full-size preview.")
            return True
        return False

    def dismiss_thumbnail(self, reason: str = "MANUAL_DISMISS") -> None:
        """Dismisses/fades the thumbnail/preview into the background."""
        self.thumbnail_state = ThumbnailOverlayState.HIDDEN
        self.active_thumbnail_bytes = None
        self.thumbnail_display_timestamp = None
        logger.info(f"[EdgeClient:{self.client_id}] Thumbnail overlay dismissed ({reason}).")

    # --- 2-Stage Timeout Architecture (Turn Silence vs Session Dormant) ---
    def record_speech(self, now: Optional[float] = None) -> None:
        """Called when user speech or audio query is actively received/streaming."""
        t = now if now is not None else time.time()
        self.last_speech_time = t
        self.last_activity_time = t
        self.is_turn_sealed = False
        self.is_listening_active = True
        self.fsm.record_speech_activity(t)

    def record_activity(self, now: Optional[float] = None) -> None:
        """Called when user interacts via hotkey, button, touch, or quick-drop."""
        t = now if now is not None else time.time()
        self.last_activity_time = t
        self.fsm.record_interaction(t)

    def seal_current_turn(self) -> None:
        """Stage 1: Seals speech turn while keeping connection active in lightweight listening state."""
        self.is_turn_sealed = True
        self.is_listening_active = True
        logger.info(
            f"[EdgeClient:{self.client_id}] Speech turn sealed (> {self.turn_silence_timeout_sec}s silence). "
            f"Connection remains active in lightweight listening state."
        )

    def check_turn_silence(self, now: Optional[float] = None) -> bool:
        """Stage 1: Check if silence duration exceeds turn_silence_timeout_sec (default 10.0s)."""
        t = now if now is not None else time.time()
        if (t - self.last_speech_time) >= self.turn_silence_timeout_sec:
            if not self.is_turn_sealed:
                self.seal_current_turn()
            return True
        return False

    def check_session_idle(self, now: Optional[float] = None) -> bool:
        """Stage 2: Check if inactivity duration exceeds session_idle_timeout_sec (default 60.0s)."""
        t = now if now is not None else time.time()
        return (t - self.last_activity_time) >= self.session_idle_timeout_sec

    async def evaluate_timeouts(self, now: Optional[float] = None) -> Dict[str, Any]:
        """
        Evaluates 2-stage timeout policy:
        - Stage 1: seals turn if silence >= turn_silence_timeout_sec (10.0s)
        - Stage 2: teardown to STANDBY_DORMANT if idle >= session_idle_timeout_sec (60.0s)
        """
        t = now if now is not None else time.time()
        res = {"turn_sealed": False, "dropped_to_dormant": False}

        if self.check_turn_silence(now=t):
            res["turn_sealed"] = True

        if self.check_session_idle(now=t):
            res["dropped_to_dormant"] = True
            await self.teardown_stream_to_dormant("SESSION_IDLE_TIMEOUT")

        return res

    def check_trigger_debounce(self, trigger_source: TriggerSource) -> bool:
        """
        Evaluate hardware trigger debounce window per trigger source.
        Returns True if trigger is accepted, False if debounced.
        """
        if self.debounce_interval_ms <= 0:
            return True

        now_ns = time.time_ns()
        last_ns = self.last_trigger_timestamps.get(trigger_source, 0)
        elapsed_ns = now_ns - last_ns
        debounce_threshold_ns = self.debounce_interval_ms * 1_000_000

        if last_ns > 0 and elapsed_ns < debounce_threshold_ns:
            logger.warning(
                f"[EdgeClient:{self.client_id}] Hardware trigger '{trigger_source.value}' debounced "
                f"({elapsed_ns / 1_000_000:.1f}ms < {self.debounce_interval_ms}ms)"
            )
            return False

        self.last_trigger_timestamps[trigger_source] = now_ns
        return True

    def serialize_camera_snapshot(
        self,
        image_bytes: bytes,
        audio_bytes: bytes = b"",
        focus_locked: bool = True,
        telemetry: Optional[TelemetryPayload] = None
    ) -> SensorPayload:
        """
        Serializes live camera snapshot and audio PCM into SensorPayload.
        Enforces [INV-05] Permission-Gated Null-Safety:
        If sensor permission is False/missing, the respective telemetry field MUST be None.
        """
        effective_telemetry = TelemetryPayload(focus_locked=focus_locked)

        if telemetry:
            effective_telemetry.lux_level = telemetry.lux_level
            effective_telemetry.focus_locked = telemetry.focus_locked

            # Permission Gate: GPS
            if self.permissions.get("gps") is True:
                effective_telemetry.gps = telemetry.gps
            else:
                effective_telemetry.gps = None

            # Permission Gate: Motion
            if self.permissions.get("motion") is True:
                effective_telemetry.motion = telemetry.motion
            else:
                effective_telemetry.motion = None

            # Permission Gate: Device Health
            if self.permissions.get("device_health") is True:
                effective_telemetry.device_health = telemetry.device_health
            else:
                effective_telemetry.device_health = None

            effective_telemetry.permissions_granted = dict(self.permissions)

        return SensorPayload(
            session_id=f"sess_{self.client_id}",
            sequence_id=int(time.time_ns() // 1_000_000),
            image=ImagePayload(data=base64.b64encode(image_bytes).decode("utf-8")),
            audio_query=AudioPayload(data=base64.b64encode(audio_bytes).decode("utf-8")),
            telemetry=effective_telemetry
        )

    async def connect(self, project_id: str = "vanilla_cabinet"):
        """Establish WebSocket connection and handshake with cloud backend."""
        self.fsm.transition_to(EdgeClientState.CONNECTING_CLOUD, "Connecting to Cloud Gateway WebSocket")
        self.notification_state = AndroidOngoingNotificationState.CONNECTING
        self.ws = await websockets.connect(self.server_uri)

        hello = ClientHello(
            client_id=self.client_id,
            client_mode=ClientMode.EDGE_FIELD,
            capabilities=ClientCapabilities(camera_pdaf=True)
        )
        hello_data = hello.to_dict()
        hello_data["project_id"] = project_id

        await self.ws.send(json.dumps(hello_data))
        raw_resp = await self.ws.recv()
        resp = json.loads(raw_resp)

        if resp.get("type") == "SERVER_READY":
            self.fsm.transition_to(EdgeClientState.CONNECTED_READY, "Handshake complete")
            self.lock_screen_status = LockScreenIndicatorStatus.READY
            self.notification_state = AndroidOngoingNotificationState.MUTED if self.is_muted else AndroidOngoingNotificationState.READY
            logger.info(f"[EdgeClient] Connected and armed on session '{resp.get('session_id')}'.")
            return resp

        raise RuntimeError(f"Unexpected server handshake response: {resp}")

    async def connect_with_retry(
        self,
        project_id: str = "vanilla_cabinet",
        max_retries: int = 3,
        initial_delay: float = 0.05,
        backoff_factor: float = 2.0
    ):
        """
        Attempt connection with exponential backoff matching EdgeClientState.CONNECTING_CLOUD.
        Cycles: CONNECTING_CLOUD -> OFFLINE_RETRY_QUEUE -> CONNECTING_CLOUD -> CONNECTED_READY.
        """
        last_error = None
        current_delay = initial_delay

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"[EdgeClient:{self.client_id}] Connection attempt {attempt}/{max_retries}...")
                return await self.connect(project_id=project_id)
            except Exception as e:
                last_error = e
                logger.warning(f"[EdgeClient:{self.client_id}] Connect attempt {attempt} failed: {e}")
                self.fsm.transition_to(
                    EdgeClientState.OFFLINE_RETRY_QUEUE,
                    f"Retry queue (attempt {attempt}/{max_retries})"
                )
                if attempt < max_retries:
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff_factor

        self.fsm.transition_to(EdgeClientState.BOOT_OFFLINE, "Retries exhausted")
        raise ConnectionError(f"Failed to connect after {max_retries} attempts: {last_error}")

    async def trigger_and_send_sensory_event(
        self,
        trigger_source: TriggerSource = TriggerSource.BT_MEDIA_BUTTON,
        image_bytes: bytes = b"mock_jpeg_data",
        audio_bytes: bytes = b"mock_pcm_audio",
        focus_locked: bool = True,
        telemetry: Optional[TelemetryPayload] = None,
        target_component_id: Optional[str] = None,
        target_pin_id: Optional[str] = None
    ) -> List[ServerAudioStreamChunk]:
        """
        Execute full Zero-UI interaction loop with hardware trigger debounce:
        1. Check hardware trigger debounce (Bluetooth or Floating Shutter)
        2. Fire trigger & update lock screen indicator to LISTENING
        3. Capture & Upload SensorPayload with [INV-05] permission-gated telemetry
        4. Update lock screen indicator to PROCESSING / STREAMING
        5. Stream audio chunks back until complete -> READY
        """
        if not self.ws:
            raise RuntimeError("Client is not connected.")

        # 1. Hardware Debounce Check
        if not self.check_trigger_debounce(trigger_source):
            raise TriggerDebouncedError(
                f"Trigger '{trigger_source.value}' debounced (< {self.debounce_interval_ms}ms)"
            )

        self.fsm.transition_to(EdgeClientState.CAPTURING, f"Triggered via {trigger_source.value}")
        self.lock_screen_status = LockScreenIndicatorStatus.LISTENING
        self.notification_state = AndroidOngoingNotificationState.THINKING

        # 2. Send Trigger
        trigger = CaptureTriggerEvent(
            trigger_source=trigger_source,
            timestamp_ns=time.time_ns(),
            action="CAPTURE_SNAPSHOT_AND_LISTEN"
        )
        await self.ws.send(trigger.to_json())

        # Wait for trigger ACK
        raw_ack = await self.ws.recv()
        ack = json.loads(raw_ack)
        if ack.get("type") != "TRIGGER_ACK":
            raise RuntimeError(f"Unexpected trigger ACK: {ack}")

        # 3. Upload Sensor Payload with Permission-Gated Telemetry [INV-05]
        self.fsm.transition_to(EdgeClientState.BUFFERING_AND_UPLOADING, "Sending Camera snapshot & audio")
        self.lock_screen_status = LockScreenIndicatorStatus.PROCESSING
        if image_bytes:
            self.render_thumbnail(image_bytes)

        payload = self.serialize_camera_snapshot(
            image_bytes=image_bytes,
            audio_bytes=audio_bytes,
            focus_locked=focus_locked,
            telemetry=telemetry
        )
        payload_data = payload.to_dict()
        if target_component_id:
            payload_data["target_component_id"] = target_component_id
        if target_pin_id:
            payload_data["target_pin_id"] = target_pin_id

        await self.ws.send(json.dumps(payload_data))

        # 4. Stream Response Playback
        self.fsm.transition_to(EdgeClientState.STREAMING_PLAYBACK, "Receiving audio stream")
        self.lock_screen_status = LockScreenIndicatorStatus.STREAMING
        chunks = []

        while True:
            raw_chunk = await self.ws.recv()
            chunk_data = json.loads(raw_chunk)
            chunk = ServerAudioStreamChunk.from_dict(chunk_data)
            chunks.append(chunk)
            self.received_chunks.append(chunk)

            if self.on_audio_received:
                self.on_audio_received(chunk)

            if chunk.is_final or chunk.safety_flag == SafetyFlag.STOP_PROBE_REQUIRED:
                break

        self.fsm.transition_to(EdgeClientState.CONNECTED_READY, "Playback finished")
        self.lock_screen_status = LockScreenIndicatorStatus.READY
        self.notification_state = AndroidOngoingNotificationState.MUTED if self.is_muted else AndroidOngoingNotificationState.READY
        return chunks

    def enter_deep_standby(self) -> bool:
        """
        Tier 3 Inactivity Watchdog: Release WakeLock, close mic hardware stream,
        reset HUD indicators, and enter STANDBY_DORMANT.
        """
        success = self.fsm.transition_to(EdgeClientState.STANDBY_DORMANT, "Inactivity timeout / deep sleep")
        if success:
            self.lock_screen_status = LockScreenIndicatorStatus.READY
            logger.info(f"[EdgeClient:{self.client_id}] Entered Tier 3 STANDBY_DORMANT.")
        return success

    def wake_from_deep_standby(self) -> bool:
        """
        Re-arm edge client from STANDBY_DORMANT upon user interaction / button trigger.
        """
        success = self.fsm.transition_to(EdgeClientState.CONNECTED_READY, "Hardware trigger wake")
        if success:
            logger.info(f"[EdgeClient:{self.client_id}] Woke from STANDBY_DORMANT -> CONNECTED_READY.")
        return success

    async def close(self):
        """Close WebSocket connection cleanly."""
        if self.ws:
            await self.ws.close()
        self.fsm.transition_to(EdgeClientState.BOOT_OFFLINE, "Disconnected")
        self.lock_screen_status = LockScreenIndicatorStatus.READY

    disconnect = close
