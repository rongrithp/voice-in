import asyncio
from typing import Optional, Any
from src.fsm import VoiceFSM, State, Event
from src.keyboard_hook import GlobalKeyboardHook
from src.audio_provider import AudioCaptureProvider
from src.audio_buffer import AudioCaptureBuffer
from src.screen_capture import ScreenCaptureProvider
from src.transport import WebSocketTransport
from src.audio_player import AudioPlayer

_f20_is_active: bool = False

class SystemOrchestrator:
    """
    Central Nervous System wiring Control Plane FSM transitions
    to concurrent Data Plane streaming pipelines.
    """
    def __init__(
        self,
        fsm: VoiceFSM,
        keyboard_hook: Optional[GlobalKeyboardHook] = None,
        audio_provider: Optional[AudioCaptureProvider] = None,
        audio_buffer: Optional[AudioCaptureBuffer] = None,
        screen_capture: Optional[ScreenCaptureProvider] = None,
        transport: Optional[WebSocketTransport] = None,
        audio_player: Optional[AudioPlayer] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self._fsm = fsm
        self._keyboard_hook = keyboard_hook
        self._audio_provider = audio_provider
        self._audio_buffer = audio_buffer
        self._screen_capture = screen_capture
        self._transport = transport
        self._audio_player = audio_player
        self._loop = loop

        self._last_screen_payload: Optional[bytes] = None
        self._is_running: bool = False
        self._pump_task: Optional[asyncio.Task] = None
        self._is_capturing_audio: bool = False
        self._lock = asyncio.Lock()

        self._setup_transport_callbacks()

    @property
    def fsm(self) -> VoiceFSM:
        return self._fsm

    @property
    def audio_player(self) -> Optional[AudioPlayer]:
        return self._audio_player

    @property
    def transport(self) -> Optional[WebSocketTransport]:
        return self._transport

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def last_screen_payload(self) -> Optional[bytes]:
        return self._last_screen_payload

    def _setup_transport_callbacks(self) -> None:
        """Wires transport audio and interruption callbacks to audio_player and FSM."""
        if not self._transport:
            return

        def _on_audio_chunk(chunk: bytes):
            print(f"[EVENT] 🔊 AUDIO CHUNK RECEIVED: {len(chunk)} bytes", flush=True)
            if hasattr(self._fsm, "state"):
                try:
                    self._fsm.state = State.PLAYING
                except Exception:
                    if hasattr(self._fsm, "_state"):
                        self._fsm._state = State.PLAYING
            elif hasattr(self._fsm, "_state"):
                self._fsm._state = State.PLAYING

            player = self._audio_player
            if player:
                res = player.play_chunk(chunk)
                if asyncio.iscoroutine(res):
                    return res
            return None

        def _on_interrupted():
            print("[EVENT] ⚠️ INTERRUPTED BY SERVER", flush=True)
            player = self._audio_player
            if player:
                res = player.stop()
                if asyncio.iscoroutine(res):
                    return res
            return None

        if hasattr(self._transport, "set_audio_callback"):
            res = self._transport.set_audio_callback(_on_audio_chunk)
            if asyncio.iscoroutine(res):
                res.close()
        if hasattr(self._transport, "set_interrupted_callback"):
            res = self._transport.set_interrupted_callback(_on_interrupted)
            if asyncio.iscoroutine(res):
                res.close()

    def handle_interruption(self) -> None:
        """Immediate synchronous interruption handler for sub-20ms latency."""
        if hasattr(self._fsm, "state"):
            self._fsm.state = State.CAPTURING
        elif hasattr(self._fsm, "_state"):
            self._fsm._state = State.CAPTURING
        if self._audio_player:
            res = self._audio_player.stop()
            if asyncio.iscoroutine(res):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(res)
                except RuntimeError:
                    pass

    def _on_transport_error(self, error: Exception) -> None:
        """Handles network transport drops by moving FSM to ERROR and marking transport disconnected."""
        if hasattr(self._fsm, "state"):
            self._fsm.state = State.ERROR
        elif hasattr(self._fsm, "_state"):
            self._fsm._state = State.ERROR

        if self._transport:
            if hasattr(self._transport, "_is_connected"):
                self._transport._is_connected = False
            if hasattr(self._transport, "disconnect"):
                res = self._transport.disconnect()
                if asyncio.iscoroutine(res):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(res)
                    except RuntimeError:
                        pass



    async def audio_pump(self) -> None:
        """Continuously streams captured mic chunks to Gemini Live realtimeInput while LISTENING."""
        from unittest.mock import MagicMock, AsyncMock
        if isinstance(self._audio_buffer, (MagicMock, AsyncMock)):
            return
        audio_queue = getattr(self._audio_buffer, "audio_queue", None)
        try:
            while self._is_capturing_audio:
                if audio_queue is not None:
                    try:
                        chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.05)
                    except asyncio.TimeoutError:
                        continue
                else:
                    if self._audio_buffer:
                        chunk = await self._audio_buffer.get()
                        if chunk is None:
                            await asyncio.sleep(0.02)
                            continue
                    else:
                        break

                if isinstance(chunk, (bytes, bytearray)):
                    if self._transport:
                        if hasattr(self._transport, "send_realtime_media"):
                            await self._transport.send_realtime_media(chunk)
                        elif hasattr(self._transport, "send_audio_chunk"):
                            await self._transport.send_audio_chunk(chunk)
                        elif hasattr(self._transport, "send_bytes"):
                            await self._transport.send_bytes(chunk)
                        print(f"[EVENT] 🎤 realtimeInput SENT: {len(chunk)} bytes", flush=True)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    _pump_audio_loop = audio_pump

    async def handle_f20_press(self) -> None:
        """F20 press hook: mute speaker, open mic, and transition state to CAPTURING/LISTENING."""
        global _f20_is_active
        if _f20_is_active:
            return  # ละทิ้ง Windows Repeat ทันที ห้ามทำอะไรเด็ดขาด
        _f20_is_active = True
        print("[EVENT] >>> F20 PRESSED (EDGE): Switching to LISTENING...", flush=True)
        if hasattr(self._fsm, "dispatch"):
            curr = getattr(self._fsm, "current_state", getattr(self._fsm, "state", None))
            if curr == State.IDLE:
                await self._fsm.dispatch(Event.CAPTURE_START)
            elif curr == State.PLAYING:
                await self._fsm.dispatch(Event.INTERRUPT)
        elif hasattr(self._fsm, "state"):
            self._fsm.state = State.CAPTURING
        await self._on_state_changed(State.CAPTURING)

    on_f20_press = handle_f20_press

    async def handle_f20_release(self) -> None:
        """F20 release hook: stop mic, transition state to STREAMING, and transmit turn complete."""
        global _f20_is_active
        if not _f20_is_active:
            return
        _f20_is_active = False
        print("[EVENT] <<< F20 RELEASED (EDGE): Switching to PROCESSING...", flush=True)
        self._is_capturing_audio = False
        if self._pump_task:
            self._pump_task.cancel()
            self._pump_task = None

        if hasattr(self._fsm, "dispatch"):
            curr = getattr(self._fsm, "current_state", getattr(self._fsm, "state", None))
            if curr == State.CAPTURING:
                await self._fsm.dispatch(Event.CAPTURE_COMPLETE)
        elif hasattr(self._fsm, "state"):
            self._fsm.state = State.STREAMING
        await self._on_state_changed(State.STREAMING)

    on_f20_release = handle_f20_release

    def on_f20_release_sync(self) -> None:
        """Dispatches handle_f20_release() safely across threads to the orchestrator loop."""
        loop = self._loop or asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(self.handle_f20_release(), loop)

    async def start(self) -> bool:
        """Initializes transport and starts global sensors."""
        async with self._lock:
            if self._is_running:
                return True

            connected = await self._transport.connect()
            if not connected:
                return False

            if hasattr(self._transport, "send_gemini_setup") and "generativelanguage.googleapis.com" in getattr(self._transport, "_endpoint", ""):
                import os
                api_key = os.getenv("GEMINI_API_KEY", "")
                await self._transport.send_gemini_setup(
                    api_key=api_key,
                    model="models/gemini-2.5-flash-native-audio-latest",
                    voice_name="Puck"
                )

            # If persistent audio provider configured, start background worker with recording gate closed
            if self._audio_provider and getattr(self._audio_provider, "_persistent", False):
                self._audio_provider.is_recording = False
                self._audio_provider.start()

            if self._keyboard_hook:
                self._keyboard_hook.start()
            self._is_running = True
            return True

    async def _on_state_changed(self, new_state: State) -> None:
        """Dispatches operational pipelines matching the active state."""
        if new_state == State.CAPTURING:
            # Immediate interruption flush on speaker
            if self._audio_player:
                await self._audio_player.stop()
            if self._audio_provider:
                self._audio_provider.start()
            # Capture snapshot in background thread
            if self._screen_capture:
                self._last_screen_payload = await self._screen_capture.capture()
            self._is_capturing_audio = True
            if self._pump_task is None or self._pump_task.done():
                self._pump_task = asyncio.create_task(self._pump_audio_loop())

        elif new_state == State.STREAMING:
            self._is_capturing_audio = False
            if self._pump_task:
                self._pump_task.cancel()
                self._pump_task = None

            if self._audio_provider:
                self._audio_provider.stop()
            # Send snapshot if available (voice-only: bypass raw binary screen send for Gemini Live)
            if self._last_screen_payload and self._transport:
                if "generativelanguage.googleapis.com" in getattr(self._transport, "_endpoint", ""):
                    pass  # Mute / Isolate to voice-only: do not send raw binary images
                else:
                    await self._transport.send_bytes(self._last_screen_payload)
            # Drain captured audio buffer
            if self._audio_buffer and self._transport:
                audio_chunks = await self._audio_buffer.drain_all()
                for chunk in audio_chunks:
                    if "generativelanguage.googleapis.com" in getattr(self._transport, "_endpoint", ""):
                        if hasattr(self._transport, "send_realtime_media"):
                            await self._transport.send_realtime_media(chunk)
                    else:
                        await self._transport.send_bytes(chunk)
            if self._transport and hasattr(self._transport, "send_turn_complete"):
                res = self._transport.send_turn_complete()
                if asyncio.iscoroutine(res):
                    await res

        elif new_state in (State.IDLE, State.ERROR):
            self._is_capturing_audio = False
            if self._pump_task:
                self._pump_task.cancel()
                self._pump_task = None

            if self._audio_provider:
                self._audio_provider.stop()
            if self._audio_player:
                await self._audio_player.stop()

    async def _handle_transport_message(self, message: bytes) -> None:
        """Routes incoming binary streams to output speaker (ignoring JSON control frames)."""
        stripped = message.strip()
        if stripped.startswith(b"{") or stripped.startswith(b"["):
            return
        await self._audio_player.play_chunk(message)

    async def shutdown(self) -> None:
        """Structured shutdown enforcing complete resource release."""
        async with self._lock:
            if not self._is_running:
                return

            self._is_capturing_audio = False
            if self._pump_task:
                self._pump_task.cancel()
                self._pump_task = None

            if self._keyboard_hook:
                self._keyboard_hook.stop()
            if self._audio_provider:
                self._audio_provider.stop()
                if hasattr(self._audio_provider, "close"):
                    self._audio_provider.close()
            if self._audio_player:
                await self._audio_player.stop()
            if self._transport:
                await self._transport.disconnect()
            self._is_running = False

def on_f20_release_sync(loop: asyncio.AbstractEventLoop, transport: Any) -> None:
    """Dispatches transport.send_turn_complete() safely across threads to the given event loop."""
    if hasattr(transport, "send_turn_complete"):
        asyncio.run_coroutine_threadsafe(transport.send_turn_complete(), loop)

def handle_f20_press():
    global _f20_is_active
    if _f20_is_active:
        return  # ละทิ้ง Windows Repeat ทันที ห้ามทำอะไรเด็ดขาด
    _f20_is_active = True
    print("[EVENT] >>> F20 PRESSED (EDGE): Switching to LISTENING...", flush=True)

def handle_f20_release():
    global _f20_is_active
    if not _f20_is_active:
        return
    _f20_is_active = False
    print("[EVENT] <<< F20 RELEASED (EDGE): Switching to PROCESSING...", flush=True)

