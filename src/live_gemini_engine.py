import asyncio
import base64
import json
import logging
import os
import threading
from typing import Callable, Optional
import websockets
import config
from src.router import SYSTEM_INSTRUCTION, is_prompt_leak_or_hallucination

logger = logging.getLogger("GeminiLiveEngine")

LIVE_SYSTEM_INSTRUCTION = "You are a real-time verbatim speech-to-text transcriber for Thai and English. Output ONLY the exact transcribed words as they are spoken. NEVER reply, converse, or explain."

class GeminiLiveStreamSession:
    """
    Bidirectional Real-Time Live Streaming Session using Gemini Multimodal Live WebSocket API.
    Streams 16kHz mono PCM chunks and receives interim tokens with < 300ms latency.
    """

    def __init__(
        self,
        on_token_callback: Callable[[str], None],
        api_key: Optional[str] = None,
        model_name: Optional[str] = None
    ):
        self.on_token_callback = on_token_callback
        self.api_key = api_key or getattr(config, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        self.model_name = model_name or getattr(config, "GEMINI_LIVE_MODEL", "gemini-2.0-flash")
        
        self._ws = None
        self._loop = None
        self._thread = None
        self._audio_queue: Optional[asyncio.Queue] = None
        self._running = threading.Event()
        self._ready_event = threading.Event()
        self._lock = threading.Lock()

    def start(self, timeout: float = 3.0) -> bool:
        """Starts the background asyncio event loop and connects WebSocket session."""
        with self._lock:
            if self._running.is_set():
                return True
            self._running.set()
            self._ready_event.clear()
            self._thread = threading.Thread(target=self._run_event_loop, daemon=True, name="GeminiLiveWSThread")
            self._thread.start()

        # Wait for setup completion or timeout
        ready = self._ready_event.wait(timeout=timeout)
        if not ready:
            logger.warning("[GeminiLiveWS] Connection setup timed out, continuing in background.")
        return ready

    def _run_event_loop(self):
        """Dedicated thread event loop for WebSocket streaming."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._session_coroutine())
        except Exception as e:
            logger.error(f"[GeminiLiveWS Loop Error]: {e}")
        finally:
            self._loop.close()

    async def _session_coroutine(self):
        """Manages WebSocket connection, setup handshake, and parallel send/recv coroutines."""
        if not self.api_key:
            logger.error("[GeminiLiveWS] No GEMINI_API_KEY configured.")
            self._running.clear()
            self._ready_event.set()
            return

        model_path = self.model_name if self.model_name.startswith("models/") else f"models/{self.model_name}"
        url = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={self.api_key}"

        self._audio_queue = asyncio.Queue()

        try:
            async with websockets.connect(url, max_size=10 * 1024 * 1024) as ws:
                self._ws = ws

                # 1. Send Setup Message
                setup_msg = {
                    "setup": {
                        "model": model_path,
                        "generationConfig": {
                            "responseModalities": ["TEXT"],
                            "temperature": 0.0
                        },
                        "systemInstruction": {
                            "parts": [
                                {
                                    "text": LIVE_SYSTEM_INSTRUCTION
                                }
                            ]
                        }
                    }
                }
                await ws.send(json.dumps(setup_msg))
                logger.info(f"[GeminiLiveWS] Connected to Live WebSocket with model: '{model_path}'")
                self._ready_event.set()

                # 2. Run Sender and Receiver tasks concurrently
                send_task = asyncio.create_task(self._send_loop())
                recv_task = asyncio.create_task(self._recv_loop())

                done, pending = await asyncio.wait(
                    [send_task, recv_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                for task in pending:
                    task.cancel()
        except Exception as e:
            logger.warning(f"[GeminiLiveWS Connection Notice]: {e}")
        finally:
            self._running.clear()
            self._ready_event.set()

    async def _send_loop(self):
        """Asynchronously pulls audio frames from queue and pushes to WebSocket."""
        while self._running.is_set():
            try:
                # Poll with short timeout to allow checking running flag
                pcm_bytes = await asyncio.wait_for(self._audio_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if not pcm_bytes or not self._ws:
                continue

            b64_audio = base64.b64encode(pcm_bytes).decode("utf-8")
            media_msg = {
                "realtimeInput": {
                    "audio": {
                        "mimeType": "audio/pcm;rate=16000",
                        "data": b64_audio
                    }
                }
            }
            try:
                await self._ws.send(json.dumps(media_msg))
            except Exception as e:
                logger.debug(f"[GeminiLiveWS Send Error]: {e}")
                break

    async def _recv_loop(self):
        """Asynchronously reads incoming responses from WebSocket and dispatches tokens."""
        while self._running.is_set() and self._ws:
            try:
                raw_msg = await self._ws.recv()
                logger.info(f"[GeminiLiveWS Inbound] {raw_msg}")
                data = json.loads(raw_msg)
                
                # Check for serverContent / modelTurn parts
                server_content = data.get("serverContent", {})
                model_turn = server_content.get("modelTurn", {})
                parts = model_turn.get("parts", [])
                
                # Check direct candidates/parts fallback
                if not parts and "candidates" in data:
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])

                for part in parts:
                    token_text = part.get("text", "")
                    if token_text:
                        if not is_prompt_leak_or_hallucination(token_text):
                            if self.on_token_callback:
                                try:
                                    self.on_token_callback(token_text)
                                except Exception as cb_err:
                                    logger.error(f"[GeminiLiveWS Callback Error]: {cb_err}")
                        else:
                            logger.info(f"[GeminiLiveWS Filter] Suppressed hallucination token: '{token_text}'")
            except websockets.exceptions.ConnectionClosed:
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"[GeminiLiveWS Recv Notice]: {e}")

    def send_audio_chunk(self, pcm_bytes: bytes):
        """Pushes raw 16-bit 16kHz PCM audio bytes into the async streaming queue."""
        if not self._running.is_set() or not self._loop or not self._audio_queue:
            return
        if not pcm_bytes:
            return

        def _enqueue():
            try:
                self._audio_queue.put_nowait(pcm_bytes)
            except Exception:
                pass

        try:
            self._loop.call_soon_threadsafe(_enqueue)
        except Exception:
            pass

    def stop(self):
        """Stops the live streaming session and shuts down background loop."""
        with self._lock:
            if not self._running.is_set():
                return
            self._running.clear()

        if self._loop and self._loop.is_running():
            # Send close signal
            if self._ws:
                try:
                    asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
                except Exception:
                    pass

        if self._thread and self._thread.is_alive() and threading.current_thread() != self._thread:
            self._thread.join(timeout=1.0)
        logger.info("[GeminiLiveWS] Live streaming session closed.")
