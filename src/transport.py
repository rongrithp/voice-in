import asyncio
import base64
import json
from typing import Optional, Callable, Any
import websockets

class WebSocketTransport:
    """
    Asynchronous WebSocket transport client with explicit lifecycle management
    and non-blocking send/receive capabilities.
    """
    def __init__(
        self,
        endpoint: str = "ws://127.0.0.1:8765",
        on_message: Optional[Callable[[bytes], Any]] = None,
        on_error: Optional[Callable[[Exception], Any]] = None,
        on_close: Optional[Callable[[], Any]] = None,
    ):
        self._endpoint = endpoint
        self._on_message = on_message
        self._on_error = on_error
        self._on_close = on_close

        self._audio_callback: Optional[Callable[[bytes], Any]] = None
        self._interrupted_callback: Optional[Callable[[], Any]] = None
        self._setup_complete_callback: Optional[Callable[[], Any]] = None

        self._ws: Optional[Any] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._is_connected: bool = False
        self._is_setup_complete: bool = False
        self._setup_complete_event: asyncio.Event = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def websocket(self):
        """Single source of truth active WebSocket connection."""
        return self._ws

    def is_setup_complete(self) -> bool:
        return self._is_setup_complete

    def set_audio_callback(self, callback: Optional[Callable[[bytes], Any]]) -> None:
        self._audio_callback = callback

    def set_interrupted_callback(self, callback: Optional[Callable[[], Any]]) -> None:
        self._interrupted_callback = callback

    def set_setup_complete_callback(self, callback: Optional[Callable[[], Any]]) -> None:
        self._setup_complete_callback = callback

    async def wait_for_setup_complete(self, timeout: float = 5.0) -> bool:
        """Waits until setupComplete frame is received from server or timeout."""
        try:
            await asyncio.wait_for(self._setup_complete_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def connect(self, endpoint: Optional[str] = None) -> bool:
        """Establishes WebSocket connection and begins background receive loop."""
        async with self._lock:
            if endpoint is not None:
                self._endpoint = endpoint

            if self._is_connected:
                return True

            try:
                self._ws = await websockets.connect(self._endpoint)
                self._is_connected = True
                self._is_setup_complete = False
                self._setup_complete_event.clear()
                self._receive_task = asyncio.create_task(self._listen_loop())
                return True
            except Exception as exc:
                self._is_connected = False
                self._is_setup_complete = False
                self._setup_complete_event.clear()
                self._ws = None
                if self._on_error:
                    res = self._on_error(exc)
                    if asyncio.iscoroutine(res):
                        await res
                return False

    async def _handle_incoming_text(self, msg: str) -> None:
        """Parses Gemini 2.0 Live JSON responses and routes audio chunks or interruption signals."""
        text = msg
        try:
            data = json.loads(msg)
        except Exception:
            return

        if not isinstance(data, dict):
            return

        if "setupComplete" in data:
            self._is_setup_complete = True
            self._setup_complete_event.set()
            if self._setup_complete_callback:
                res = self._setup_complete_callback()
                if asyncio.iscoroutine(res):
                    await res

        server_content = data.get("serverContent")
        if not isinstance(server_content, dict):
            return

        if server_content.get("interrupted"):
            if self._interrupted_callback:
                res = self._interrupted_callback()
                if asyncio.iscoroutine(res):
                    await res

        model_turn = server_content.get("modelTurn")
        if isinstance(model_turn, dict):
            parts = model_turn.get("parts", [])
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline_data = part.get("inlineData")
                if isinstance(inline_data, dict):
                    raw_b64 = inline_data.get("data")
                    if raw_b64:
                        try:
                            pcm_data = base64.b64decode(raw_b64)
                            print(f"[RECV] 🔊 Audio chunk from Gemini: {len(pcm_data)} bytes", flush=True)
                            if self._audio_callback:
                                res = self._audio_callback(pcm_data)
                                if asyncio.iscoroutine(res):
                                    await res
                        except Exception:
                            pass

        if server_content.get("turnComplete"):
            print("[RECV] ✅ Model finished speaking (turnComplete)", flush=True)

    async def _listen_loop(self) -> None:
        """Internal background receiver loop."""
        try:
            while self._is_connected and self._ws:
                message = await self._ws.recv()
                if isinstance(message, str):
                    await self._handle_incoming_text(message)
                elif isinstance(message, bytes):
                    try:
                        text = message.decode("utf-8")
                        await self._handle_incoming_text(text)
                    except UnicodeDecodeError:
                        pass

                if self._on_message:
                    # Convert str to bytes if necessary to maintain byte-level consistency
                    payload = message.encode("utf-8") if isinstance(message, str) else message
                    res = self._on_message(payload)
                    if asyncio.iscoroutine(res):
                        await res
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if self._on_error:
                res = self._on_error(exc)
                if asyncio.iscoroutine(res):
                    await res
        finally:
            if self._on_close and self._is_connected:
                res = self._on_close()
                if asyncio.iscoroutine(res):
                    await res

    async def send_bytes(self, data: bytes) -> bool:
        """Transmits raw binary payloads over the active socket."""
        async with self._lock:
            if not self._is_connected or not self._ws:
                return False

            if "generativelanguage.googleapis.com" in getattr(self, "_endpoint", ""):
                # Gemini Live WebSocket strictly requires text JSON frames. Binary frames are forbidden.
                return False

            try:
                await self._ws.send(data)
                return True
            except Exception as exc:
                if self._on_error:
                    res = self._on_error(exc)
                    if asyncio.iscoroutine(res):
                        await res
                return False

    async def send_gemini_setup(
        self,
        api_key: Optional[str] = None,
        model: str = "models/gemini-2.0-flash-exp",
        voice_name: str = "Puck",
    ) -> bool:
        """Sends initial Gemini Live handshake setup message."""
        async with self._lock:
            if not self._is_connected or not self._ws:
                return False

            if "generativelanguage.googleapis.com" in getattr(self, "_endpoint", ""):
                setup_payload = {
                    "setup": {
                        "model": model,
                        "generationConfig": {
                            "responseModalities": ["AUDIO"],
                            "speechConfig": {
                                "voiceConfig": {
                                    "prebuiltVoiceConfig": {
                                        "voiceName": voice_name
                                    }
                                }
                            } if voice_name else {}
                        }
                    }
                }
            else:
                setup_payload = {
                    "setup": {
                        "model": model,
                        "generation_config": {
                            "response_modalities": ["AUDIO"],
                            "speech_config": {
                                "voice_config": {
                                    "prebuilt_voice_config": {
                                        "voice_name": voice_name
                                    }
                                }
                            } if voice_name else {}
                        }
                    }
                }
            try:
                await self._ws.send(json.dumps(setup_payload))
                return True
            except Exception as exc:
                if self._on_error:
                    res = self._on_error(exc)
                    if asyncio.iscoroutine(res):
                        await res
                return False

    async def send_audio_chunk(self, pcm_bytes: bytes) -> bool:
        """Wraps 16kHz PCM audio in base64 within realtimeInput schema and transmits."""
        async with self._lock:
            if not self._is_connected or not self._ws:
                return False

            b64_data = base64.b64encode(pcm_bytes).decode("ascii")
            if "generativelanguage.googleapis.com" in getattr(self, "_endpoint", ""):
                payload = {
                    "realtimeInput": {
                        "mediaChunks": [
                            {
                                "mimeType": "audio/pcm;rate=16000",
                                "data": b64_data,
                            }
                        ]
                    }
                }
            else:
                payload = {
                    "realtime_input": {
                        "media_chunks": [
                            {
                                "mime_type": "audio/pcm;rate=16000",
                                "data": b64_data,
                            }
                        ]
                    }
                }
            try:
                await self._ws.send(json.dumps(payload))
                if not hasattr(self, "_audio_chunk_counter"):
                    self._audio_chunk_counter = 0
                self._audio_chunk_counter += 1
                return True
            except Exception as exc:
                if self._on_error:
                    res = self._on_error(exc)
                    if asyncio.iscoroutine(res):
                        await res
                return False

    async def send_realtime_media(self, pcm_bytes: bytes) -> bool:
        """Transmits realtime audio chunk wrapped in realtime_input schema to WebSocket."""
        return await self.send_audio_chunk(pcm_bytes)

    async def send_json(self, payload: dict) -> bool:
        """Serializes payload to JSON and transmits over active WebSocket."""
        async with self._lock:
            if not self._is_connected or not self._ws:
                return False
            try:
                await self._ws.send(json.dumps(payload))
                return True
            except Exception as exc:
                if self._on_error:
                    res = self._on_error(exc)
                    if asyncio.iscoroutine(res):
                        await res
                return False

    async def send_audio_stream_end(self) -> bool:
        """Transmits audioStreamEnd inside realtime_input to signal end of user audio input."""
        return await self.send_turn_complete()

    async def send_turn_complete(self) -> bool:
        """Transmits turnComplete / audioStreamEnd to complete the active turn."""
        if "generativelanguage.googleapis.com" in getattr(self, "_endpoint", ""):
            payload = {"realtime_input": {"audioStreamEnd": True}}
        else:
            payload = {"clientContent": {"turnComplete": True}}
        success = await self.send_json(payload)
        print("[EVENT] >>> WebSocket turnComplete PAYLOAD SENT TO SERVER!", flush=True)
        return success

    async def receive_messages(self) -> None:
        """Continuous background receiver loop for reading inbound WebSocket frames."""
        await self._listen_loop()

    async def send_image_frame(self, jpeg_bytes: bytes, mime_type: str = "image/jpeg") -> bool:
        """Wraps JPEG screen/video frame in base64 within realtime_input schema and transmits."""
        async with self._lock:
            if not self._is_connected or not self._ws:
                return False

            b64_data = base64.b64encode(jpeg_bytes).decode("ascii")
            if "generativelanguage.googleapis.com" in getattr(self, "_endpoint", ""):
                payload = {
                    "realtimeInput": {
                        "mediaChunks": [
                            {
                                "mimeType": mime_type,
                                "data": b64_data,
                            }
                        ]
                    }
                }
            else:
                chunk_data = {
                    "mime_type": mime_type,
                    "mimeType": mime_type,
                    "data": b64_data,
                }
                payload = {
                    "realtime_input": {
                        "media_chunks": [chunk_data],
                        "mediaChunks": [chunk_data],
                    }
                }
            try:
                await self._ws.send(json.dumps(payload))
                return True
            except Exception as exc:
                if self._on_error:
                    res = self._on_error(exc)
                    if asyncio.iscoroutine(res):
                        await res
                return False

    async def disconnect(self) -> None:
        """Idempotent teardown of active stream and cancellation of listen loop."""
        async with self._lock:
            if not self._is_connected and not self._ws:
                return

            self._is_connected = False
            self._is_setup_complete = False
            self._setup_complete_event.clear()

            if self._receive_task:
                self._receive_task.cancel()
                try:
                    await self._receive_task
                except asyncio.CancelledError:
                    pass
                self._receive_task = None

            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None

