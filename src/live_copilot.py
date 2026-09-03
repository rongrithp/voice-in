import asyncio
import inspect
import io
import json
import logging
import os
import sys
import tempfile
import threading
import time
from typing import Optional

import certifi
import cv2
import mss
import numpy as np
from PIL import Image
import sounddevice as sd

from google import genai
from google.genai import types

import config
from config import LIVE_COPILOT_CONFIG, build_system_instruction
from src.live_memory import LiveSessionMemory
from src.audio import WindHarmonicsFilter

logger = logging.getLogger("LiveCopilot")

# Fast local SSD certificate caching to eliminate 50s+ network/virtual drive delays on Windows
def _setup_fast_local_ssl():
    try:
        local_cert_path = os.path.join(tempfile.gettempdir(), "voicein_cacert.pem")
        cert_src = certifi.where()
        if not os.path.exists(local_cert_path) or os.path.getsize(local_cert_path) != os.path.getsize(cert_src):
            with open(cert_src, "rb") as f_in, open(local_cert_path, "wb") as f_out:
                f_out.write(f_in.read())
        os.environ["SSL_CERT_FILE"] = local_cert_path
        os.environ["REQUESTS_CA_BUNDLE"] = local_cert_path
    except Exception:
        os.environ["SSL_CERT_FILE"] = certifi.where()
        os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

_setup_fast_local_ssl()

for _proxy_var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
    os.environ[_proxy_var] = ""
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

_GLOBAL_LIVE_CLIENT: Optional[genai.Client] = None
_GLOBAL_BACKEND_DESC: Optional[str] = None
_CLIENT_INIT_LOCK = threading.Lock()


def _initialize_global_live_client():
    """Initializes and caches the Google GenAI Client singleton eagerly."""
    global _GLOBAL_LIVE_CLIENT, _GLOBAL_BACKEND_DESC
    if _GLOBAL_LIVE_CLIENT is not None:
        return _GLOBAL_LIVE_CLIENT, _GLOBAL_BACKEND_DESC

    with _CLIENT_INIT_LOCK:
        if _GLOBAL_LIVE_CLIENT is not None:
            return _GLOBAL_LIVE_CLIENT, _GLOBAL_BACKEND_DESC

        api_key = (getattr(config, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")).strip().strip('"').strip("'")
        if api_key:
            logger.info("[LiveCopilot Auth] Using GEMINI_API_KEY (Direct AI Studio API, v1alpha)")
            _GLOBAL_LIVE_CLIENT = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(api_version="v1alpha")
            )
            _GLOBAL_BACKEND_DESC = "Google AI Studio Direct (v1alpha)"
            return _GLOBAL_LIVE_CLIENT, _GLOBAL_BACKEND_DESC

        cred_path = config.get_google_credentials_path()
        if cred_path and os.path.exists(cred_path):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cred_path
            project_id = None
            try:
                with open(cred_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    project_id = data.get("project_id")
            except Exception:
                pass
            gcp_project = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "voice-in-app")
            try:
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_file(
                    cred_path,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                logger.info(f"[LiveCopilot Auth] Using Vertex AI Explicit Service Account ({gcp_project}, v1alpha)")
                _GLOBAL_LIVE_CLIENT = genai.Client(
                    vertexai=True,
                    project=gcp_project,
                    location="us-central1",
                    credentials=creds,
                    http_options=types.HttpOptions(api_version="v1alpha")
                )
            except Exception as e:
                logger.warning(f"[LiveCopilot Auth Notice] Direct credentials load fallback: {e}")
                _GLOBAL_LIVE_CLIENT = genai.Client(
                    vertexai=True,
                    project=gcp_project,
                    location="us-central1",
                    http_options=types.HttpOptions(api_version="v1alpha")
                )

            _GLOBAL_BACKEND_DESC = f"Vertex AI ({gcp_project}, v1alpha)"
            return _GLOBAL_LIVE_CLIENT, _GLOBAL_BACKEND_DESC

        raise RuntimeError("Neither GEMINI_API_KEY nor Service Account credentials found.")


# Module-level background eager pre-initialization (bypassed under pytest)
def _safe_module_init():
    if "pytest" in sys.modules or "pytest" in sys.argv[0].lower() or os.getenv("PYTEST_CURRENT_TEST"):
        return
    try:
        _initialize_global_live_client()
    except Exception:
        pass

threading.Thread(target=_safe_module_init, daemon=True, name="LiveCopilotModulePreInit").start()


def sound_feedback(freq: int, ms: int):
    """Play asynchronous system beep feedback on Windows."""
    def _beep():
        try:
            import winsound
            winsound.Beep(freq, ms)
        except Exception:
            pass
    threading.Thread(target=_beep, daemon=True).start()


def _safe_destroy_cv2_windows(win_name: Optional[str] = "Gemini Vision Stream"):
    """
    Safely and completely closes OpenCV preview windows on Windows OS.
    Pumps cv2.waitKey(1) and issues Win32 WM_CLOSE to guarantee instant non-blocking window destruction.
    """
    try:
        if win_name:
            try:
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(None, win_name)
                if hwnd:
                    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            except Exception:
                pass
            try:
                cv2.destroyWindow(win_name)
            except Exception:
                pass
        cv2.destroyAllWindows()
        for _ in range(5):
            cv2.waitKey(1)
    except Exception as e:
        logger.debug(f"[LiveCopilot] OpenCV window cleanup notice: {e}")


class LiveCopilotSession:
    """
    Multimodal Live Co-pilot Session Controller.
    Manages full-duplex real-time streaming of microphone audio and screen captures
    to Gemini 2.5 Live API with native barge-in support and thread-isolated execution.
    """

    def __init__(
        self,
        target_monitor: Optional[int] = None,
        fps: Optional[int] = None,
        model_name: Optional[str] = None,
        show_preview: Optional[bool] = None
    ):
        self.target_monitor = target_monitor if target_monitor is not None else getattr(config, "DEFAULT_TARGET_MONITOR", getattr(config, "GEMINI_LIVE_TARGET_MONITOR", 1))
        self.fps = fps or getattr(config, "GEMINI_LIVE_FPS", 0.67)
        self.model_name = model_name or getattr(config, "GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
        self.show_preview = getattr(config, "SHOW_VISION_PREVIEW", True) if show_preview is None else show_preview
        # DSP & noise gate — sourced from LIVE_COPILOT_CONFIG for single-dict access;
        # flat config constants kept for backward-compat but LIVE_COPILOT_CONFIG takes precedence.
        self.noise_threshold = float(LIVE_COPILOT_CONFIG.get("rms_speech_threshold", getattr(config, "GEMINI_LIVE_RMS_THRESHOLD", 2500.0)))
        self.barge_in_threshold = float(LIVE_COPILOT_CONFIG.get("barge_in_threshold", getattr(config, "GEMINI_LIVE_BARGE_IN_THRESHOLD", 3500.0)))
        self.enable_wind_filter = bool(LIVE_COPILOT_CONFIG.get("enable_wind_filter", getattr(config, "ENABLE_WIND_FILTER", True)))
        self.wind_cutoff_hz = float(LIVE_COPILOT_CONFIG.get("wind_filter_cutoff_hz", getattr(config, "WIND_FILTER_CUTOFF_HZ", 80.0)))
        
        self._is_running = False
        self._is_connected = False
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._client: Optional[genai.Client] = None
        self._backend_desc: Optional[str] = None
        self._is_ai_speaking = False
        self._is_speaking_active = threading.Event()
        self._is_speaking_active.clear()
        self._last_playback_time = 0.0
        self._last_ai_speech_end_time = 0.0
        self._last_barge_in_time = 0.0
        self._mic_stream = None
        self._speaker_stream = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._audio_in_queue: Optional[asyncio.Queue] = None
        self._audio_out_queue: Optional[asyncio.Queue] = None
        self._worker_tasks: list[asyncio.Task] = []
        self._active_session = None
        self._active_conn_ctx = None
        self._mouse_listener = None
        self.memory = LiveSessionMemory()
        self._session_transcript: list[dict[str, Any]] = []
        self.on_audio_level = None
        self.on_handshake = None
        self.on_connected = None
        self.on_error = None
        self.on_traffic_state = None
        self._traffic_debounce_timer: Optional[threading.Timer] = None
        self._is_transmitting: bool = False
        self._override_consecutive_frames: int = 0

        # Instant reference to pre-warmed client if already ready
        if _GLOBAL_LIVE_CLIENT is not None:
            self._client = _GLOBAL_LIVE_CLIENT
            self._backend_desc = _GLOBAL_BACKEND_DESC

    def _flush_memory_snapshot(self):
        """Flushes in-memory dialogue turns to persistent rolling memory storage."""
        with self._lock:
            if self._session_transcript:
                try:
                    self.memory.save_session_snapshot(self._session_transcript)
                    logger.info(f"[LiveMemory] Flushed session snapshot ({len(self._session_transcript)} turns saved).")
                except Exception as e:
                    logger.debug(f"[LiveMemory Notice] Could not save session snapshot: {e}")
                self._session_transcript = []

    def _handle_interruption(self, source: str = "user_speech"):
        """Forces state machine self-healing and barge-in cutoff."""
        with self._lock:
            self._is_speaking_active.clear()
            self._is_ai_speaking = False
            self._override_consecutive_frames = 0
        if self._audio_out_queue:
            while not self._audio_out_queue.empty():
                try:
                    self._audio_out_queue.get_nowait()
                    self._audio_out_queue.task_done()
                except Exception:
                    break
        if getattr(self, "_trigger_barge_in_fn", None) and callable(self._trigger_barge_in_fn):
            try:
                self._trigger_barge_in_fn(source)
            except Exception as e:
                logger.debug(f"[LiveCopilot Manual Interruption Notice] {e}")

    def _safe_put_audio_in(self, data: bytes):
        """Threadsafe non-blocking audio enqueue with QueueFull overflow protection."""
        if self._audio_in_queue is None:
            return
        try:
            self._audio_in_queue.put_nowait(data)
        except (asyncio.QueueFull, Exception):
            try:
                self._audio_in_queue.get_nowait()
                self._audio_in_queue.task_done()
            except Exception:
                pass
            try:
                self._audio_in_queue.put_nowait(data)
            except Exception:
                pass

    def _enqueue_audio_in(self, data: bytes):
        """Enqueues incoming audio chunk safely either via loop or directly."""
        if self._async_loop and self._async_loop.is_running():
            self._async_loop.call_soon_threadsafe(self._safe_put_audio_in, data)
        else:
            self._safe_put_audio_in(data)

    def mic_callback(self, indata, frames=1024, time_info=None, status=None):
        """Processes an incoming raw audio block from the microphone."""
        if not self._stop_event.is_set() and self._is_running:
            try:
                raw_bytes = bytes(indata)
                wind_filter = getattr(self, "_wind_filter", None) if getattr(self, "enable_wind_filter", True) else None
                if wind_filter is not None:
                    raw_bytes = wind_filter.process_pcm_bytes(raw_bytes)

                audio_data = np.frombuffer(raw_bytes, dtype=np.int16)
                rms = float(np.sqrt(np.mean(audio_data.astype(np.float64)**2))) if len(audio_data) > 0 else 0.0

                if hasattr(self, "on_audio_level") and self.on_audio_level:
                    try:
                        self.on_audio_level(rms)
                    except Exception:
                        pass

                # Vocal Barge-in via High-Threshold Energy Gate:
                # When AI is speaking (playback active):
                #   If rms >= 4000.0 for 2 consecutive frames, pass raw mic PCM upstream to trigger server-side VAD barge-in.
                #   Otherwise, suppress to zero-PCM silence to prevent speaker echo bleed.
                # When AI is NOT speaking:
                #   Always pass raw mic PCM directly.
                if self._is_ai_speaking or self._is_speaking_active.is_set():
                    if rms >= 4000.0:
                        self._override_consecutive_frames += 1
                        if self._override_consecutive_frames >= 2:
                            self._enqueue_audio_in(raw_bytes)
                        else:
                            silence_bytes = b"\x00" * len(raw_bytes)
                            self._enqueue_audio_in(silence_bytes)
                    else:
                        self._override_consecutive_frames = 0
                        silence_bytes = b"\x00" * len(raw_bytes)
                        self._enqueue_audio_in(silence_bytes)
                else:
                    self._override_consecutive_frames = 0
                    self._enqueue_audio_in(raw_bytes)
            except Exception:
                pass

    def warmup(self):
        """Zero-delay no-op: background warmup removed to guarantee instant direct connection."""
        pass

    def set_traffic_state(self, is_transmitting: bool):
        """Notifies registered listeners of live network/audio/vision traffic state."""
        self._is_transmitting = is_transmitting
        if callable(self.on_traffic_state):
            try:
                self.on_traffic_state(is_transmitting)
            except Exception as e:
                logger.debug(f"[LiveCopilot Traffic Callback Error] {e}")

    def notify_traffic(self, debounce_ms: float = 300.0):
        """
        Fires set_traffic_state(True) on active data transfer and schedules
        automatic reset back to False after debounce_ms (default 300ms) inactivity timeout.
        """
        if not self._is_running:
            return

        if not self._is_transmitting:
            self.set_traffic_state(True)

        if self._traffic_debounce_timer is not None:
            self._traffic_debounce_timer.cancel()

        def _revert():
            if self._is_running:
                self.set_traffic_state(False)

        self._traffic_debounce_timer = threading.Timer(debounce_ms / 1000.0, _revert)
        self._traffic_debounce_timer.daemon = True
        self._traffic_debounce_timer.start()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def start(self) -> bool:
        """Starts the multimodal live co-pilot session in a dedicated background thread (< 5ms dispatch)."""
        with self._lock:
            if self._is_running:
                logger.info("[LiveCopilot] Session already active.")
                return True

            logger.info("[LiveCopilot] Starting Multimodal Live Session (Instant Direct Dispatch)...")
            self._stop_event.clear()
            self._is_running = True
            self._is_ai_speaking = False
            self._is_speaking_active.clear()
            self._last_playback_time = 0.0
            self._last_ai_speech_end_time = 0.0
            sound_feedback(880, 100) # High pitch chime on start

            self._worker_thread = threading.Thread(
                target=self._run_thread,
                name="LiveCopilotWorker",
                daemon=True
            )
            self._worker_thread.start()
            return True

    async def run(self):
        """Runs the multimodal live session lifecycle directly in an asyncio event loop."""
        with self._lock:
            if self._is_running:
                logger.info("[LiveCopilot] Session already active.")
                return
            self._stop_event.clear()
            self._is_running = True
            self._is_ai_speaking = False
            self._is_speaking_active.clear()
            self._last_playback_time = 0.0
            self._last_ai_speech_end_time = 0.0
            sound_feedback(880, 100)

        try:
            self._async_loop = asyncio.get_running_loop()
            await self._async_live_loop()
        except (asyncio.CancelledError, RuntimeError) as ex:
            err_msg = str(ex).lower()
            if "event loop stopped" in err_msg or isinstance(ex, asyncio.CancelledError):
                logger.debug(f"[LiveCopilot] Loop terminated via cancellation: {ex}")
            else:
                raise
        finally:
            self._is_running = False
            self._is_connected = False
            self._async_loop = None
            if self.show_preview:
                _safe_destroy_cv2_windows("Gemini Vision Stream")
            self._flush_memory_snapshot()

    def stop(self) -> bool:
        """Stops the active live co-pilot session forcefully, purges queues, cancels tasks, and closes audio hardware streams."""
        with self._lock:
            self._is_speaking_active.clear()
            was_running = self._is_running or (self._worker_thread is not None and self._worker_thread.is_alive())
            has_resources = (
                self._speaker_stream is not None
                or self._mic_stream is not None
                or bool(self._worker_tasks)
                or self._active_session is not None
                or bool(self._session_transcript)
            )
            if not was_running and not has_resources:
                return False

            logger.info("[LiveCopilot] Forcefully Stopping Multimodal Live Session & Purging Audio Streams...")
            self._is_running = False
            self._is_connected = False
            self._is_ai_speaking = False
            self._override_consecutive_frames = 0
            self._stop_event.set()

            if self._traffic_debounce_timer is not None:
                self._traffic_debounce_timer.cancel()
                self._traffic_debounce_timer = None
            self.set_traffic_state(False)

            # 1. Force immediate purge of audio queues
            if self._audio_out_queue:
                while not self._audio_out_queue.empty():
                    try:
                        self._audio_out_queue.get_nowait()
                        self._audio_out_queue.task_done()
                    except Exception:
                        break

            if self._audio_in_queue:
                while not self._audio_in_queue.empty():
                    try:
                        self._audio_in_queue.get_nowait()
                        self._audio_in_queue.task_done()
                    except Exception:
                        break

            # 2. Stop mouse listener thread if running
            if getattr(self, "_mouse_listener", None) is not None:
                try:
                    self._mouse_listener.stop()
                except Exception:
                    pass
                self._mouse_listener = None

            # 3. Force close audio output & input streams directly to prevent blocked executors and PortAudio threads
            if self._speaker_stream:
                try:
                    if hasattr(self._speaker_stream, "abort"):
                        self._speaker_stream.abort()
                except Exception as ex:
                    logger.debug(f"[Speaker Abort Notice]: {ex}")
                try:
                    if hasattr(self._speaker_stream, "stop"):
                        self._speaker_stream.stop()
                except Exception:
                    pass
                try:
                    if hasattr(self._speaker_stream, "close"):
                        self._speaker_stream.close()
                except Exception:
                    pass
                self._speaker_stream = None

            if self._mic_stream:
                try:
                    if hasattr(self._mic_stream, "abort"):
                        self._mic_stream.abort()
                except Exception as ex:
                    logger.debug(f"[Mic Abort Notice]: {ex}")
                try:
                    if hasattr(self._mic_stream, "stop"):
                        self._mic_stream.stop()
                except Exception:
                    pass
                try:
                    if hasattr(self._mic_stream, "close"):
                        self._mic_stream.close()
                except Exception:
                    pass
                self._mic_stream = None

            # 4. Explicitly close active WebSocket session immediately
            active_session = getattr(self, "_active_session", None)
            if active_session:
                try:
                    ws = getattr(active_session, "_ws", None)
                    if ws:
                        transport = getattr(ws, "transport", None)
                        if transport and hasattr(transport, "close"):
                            transport.close()
                except Exception:
                    pass
                if hasattr(active_session, "close"):
                    try:
                        res = active_session.close()
                        if inspect.isawaitable(res) and self._async_loop and self._async_loop.is_running():
                            asyncio.run_coroutine_threadsafe(res, self._async_loop)
                    except Exception:
                        pass
                self._active_session = None

            # 5. Cancel all pending asyncio tasks in the event loop
            if self._async_loop and self._async_loop.is_running():
                for task in list(self._worker_tasks):
                    if not task.done():
                        try:
                            self._async_loop.call_soon_threadsafe(task.cancel)
                        except Exception:
                            pass
                try:
                    for task in list(asyncio.all_tasks(self._async_loop)):
                        if not task.done():
                            try:
                                self._async_loop.call_soon_threadsafe(task.cancel)
                            except Exception:
                                pass
                except Exception:
                    pass
                try:
                    self._async_loop.call_soon_threadsafe(self._async_loop.stop)
                except Exception:
                    pass

            try:
                sound_feedback(440, 100) # Low pitch chime on stop
            except Exception:
                pass

            thread_to_join = self._worker_thread
            self._worker_thread = None

        _safe_destroy_cv2_windows("Gemini Vision Stream")

        if thread_to_join and thread_to_join.is_alive() and threading.current_thread() != thread_to_join:
            thread_to_join.join(timeout=1.0)

        self._flush_memory_snapshot()
        logger.info("[LiveCopilot] Session stopped and all streams purged cleanly.")
        return True

    def toggle(self) -> bool:
        """发挥 co-pilot session state between ON and OFF."""
        if self.is_running:
            self.stop()
            return False
        else:
            return self.start()

    def _run_thread(self):
        """Dedicated thread entry point running isolated asyncio event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._async_loop = loop
        try:
            loop.run_until_complete(self._async_live_loop())
        except (asyncio.CancelledError, RuntimeError) as ex:
            err_msg = str(ex).lower()
            if "event loop stopped" in err_msg or isinstance(ex, asyncio.CancelledError):
                logger.debug(f"[LiveCopilot] Loop terminated via cancellation: {ex}")
            else:
                logger.error(f"[LiveCopilot Worker Error] {ex}", exc_info=True)
        except Exception as e:
            logger.error(f"[LiveCopilot Worker Error] {e}", exc_info=True)
        finally:
            self._is_running = False
            self._is_connected = False
            try:
                # Cancel all pending tasks cleanly before closing the loop
                pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
                self._async_loop = None

            if self.show_preview:
                _safe_destroy_cv2_windows("Gemini Vision Stream")
            self._flush_memory_snapshot()

    def _resolve_client(self):
        """Resolves Google GenAI Client in O(1) time using pre-warmed singleton."""
        if self._client is not None:
            return self._client, self._backend_desc

        client, desc = _initialize_global_live_client()
        self._client = client
        self._backend_desc = desc
        return self._client, self._backend_desc

    async def _async_live_loop(self):
        """Main async full-duplex multimodal live streaming pipeline."""
        client, backend_desc = self._resolve_client()
        logger.info(f"[LiveCopilot] Backend active: {backend_desc}")

        candidate_models = [
            self.model_name,
            "gemini-3.1-flash-live-preview",
            "gemini-2.0-flash-exp",
            "gemini-2.5-flash",
            "gemini-2.5-flash-native-audio-latest",
            "gemini-2.5-flash-native-audio-preview-12-2025"
        ]
        # Deduplicate while preserving order
        seen = set()
        candidates = [m for m in candidate_models if m and not (m in seen or seen.add(m))]

        rolling_context = self.memory.get_rolling_context(max_sessions=2)
        # Assemble system instruction dynamically from LIVE_COPILOT_CONFIG —
        # pacing, persona, speech invariants, and optional session memory are
        # all injected here without touching hardcoded strings.
        system_instruction_text = build_system_instruction(LIVE_COPILOT_CONFIG, rolling_context)
        if rolling_context:
            logger.info("[LiveMemory] Injected short-term session memory into system instruction.")

        live_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=LIVE_COPILOT_CONFIG.get("voice_name", "Aoede")
                    )
                )
            ),
            thinking_config=types.ThinkingConfig(
                thinking_budget=0
            ),
            system_instruction=types.Content(
                parts=[types.Part.from_text(text=system_instruction_text)]
            )
        )

        audio_in_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=300)
        audio_out_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1000)
        loop = asyncio.get_running_loop()
        self._async_loop = loop
        self._audio_in_queue = audio_in_queue
        self._audio_out_queue = audio_out_queue

        def _clear_queues():
            """Empties in/out audio queues cleanly to prevent stale audio backlog on reconnection."""
            while not audio_in_queue.empty():
                try:
                    audio_in_queue.get_nowait()
                    audio_in_queue.task_done()
                except Exception:
                    break
            while not audio_out_queue.empty():
                try:
                    audio_out_queue.get_nowait()
                    audio_out_queue.task_done()
                except Exception:
                    break

        _safe_put_audio_in = self._safe_put_audio_in

        # Real-time Wind Harmonics & Low-Frequency Rumble Elimination
        enable_wind = getattr(self, "enable_wind_filter", getattr(config, "ENABLE_WIND_FILTER", True))
        wind_cutoff = float(getattr(self, "wind_cutoff_hz", getattr(config, "WIND_FILTER_CUTOFF_HZ", 80.0)))
        copilot_wind_filter = WindHarmonicsFilter(cutoff_hz=wind_cutoff, sample_rate=16000) if enable_wind else None
        self._wind_filter = copilot_wind_filter

        def mic_callback(indata, frames, time_info, status):
            self.mic_callback(indata, frames, time_info, status)

        mic_stream = sd.RawInputStream(
            samplerate=16000,
            channels=1,
            dtype="int16",
            blocksize=1024,
            callback=mic_callback
        )
        speaker_stream = sd.RawOutputStream(
            samplerate=24000,
            channels=1,
            dtype="int16",
            blocksize=2048
        )
        self._mic_stream = mic_stream
        self._speaker_stream = speaker_stream

        barge_in_event = asyncio.Event()
        turn_interrupted = threading.Event()

        def _trigger_barge_in(source: str = "user_speech"):
            now = time.perf_counter()
            is_active = self._is_ai_speaking or self._is_speaking_active.is_set() or not audio_out_queue.empty()
            should_log = is_active and (now - self._last_barge_in_time > 0.20)
            self._last_barge_in_time = now

            # State Machine Self-Healing:
            # Force clear queue, reset speaking events, and reset flags immediately
            turn_interrupted.set()
            barge_in_event.set()
            _flush_audio_out()
            _set_ai_speaking(False)
            self._is_speaking_active.clear()
            self._is_ai_speaking = False
            self._override_consecutive_frames = 0

            # Auto-clear turn_interrupted after 200ms recovery window so future model turns are never blocked
            try:
                loop.call_later(0.20, turn_interrupted.clear)
            except Exception:
                turn_interrupted.clear()

            # Ensure speaker_stream is stopped cleanly and restarted so playback consumer loop does not terminate
            if speaker_stream:
                try:
                    if hasattr(speaker_stream, "abort"):
                        speaker_stream.abort()
                    elif hasattr(speaker_stream, "stop"):
                        speaker_stream.stop()
                except Exception as ex:
                    logger.debug(f"[Barge-in Stream Stop Notice] {ex}")
                try:
                    if hasattr(speaker_stream, "start") and not getattr(speaker_stream, "active", False):
                        speaker_stream.start()
                except Exception as ex:
                    logger.debug(f"[Barge-in Stream Restart Notice] {ex}")

            # Signal interruption to Gemini Live server if session active
            active_s = getattr(self, "_active_session", None)
            if active_s and hasattr(active_s, "send"):
                try:
                    interrupt_content = types.LiveClientContent(turns=[], turn_complete=False)
                    asyncio.run_coroutine_threadsafe(
                        active_s.send(input=interrupt_content, end_of_turn=False),
                        loop
                    )
                except Exception as send_ex:
                    logger.debug(f"[Barge-in Server Signal Notice] {send_ex}")

            if should_log:
                logger.info(f"[⚡ Barge-in Triggered] Interrupted by {source} -> Cutting off audio playback instantly.")
                print("\n[⚡ Barge-in Triggered] Interrupted by user speech!", flush=True)
                with self._lock:
                    self._session_transcript.append({"role": "user", "text": "[User interrupted model speech / Barge-in]"})

        _handle_interruption = _trigger_barge_in
        self._trigger_barge_in_fn = _trigger_barge_in

        def _set_ai_speaking(speaking: bool):
            if speaking:
                self._is_speaking_active.set()
                self._is_ai_speaking = True
            else:
                if self._is_speaking_active.is_set() or self._is_ai_speaking:
                    self._last_ai_speech_end_time = time.perf_counter()
                self._is_speaking_active.clear()
                self._is_ai_speaking = False

        def _flush_audio_out():
            _set_ai_speaking(False)
            while not audio_out_queue.empty():
                try:
                    audio_out_queue.get_nowait()
                    audio_out_queue.task_done()
                except Exception:
                    break

        async def audio_input_worker(session, active_event: asyncio.Event):
            silence_heartbeat = b"\x00" * 2048  # 1024 samples @ 16kHz 16-bit mono = 64ms zero-PCM
            while active_event.is_set() and not self._stop_event.is_set():
                try:
                    chunk = await asyncio.wait_for(audio_in_queue.get(), timeout=0.1)
                    blob = types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                    if hasattr(session, "send_realtime_input"):
                        await session.send_realtime_input(audio=blob)
                    else:
                        await session.send(input={"data": chunk, "mime_type": "audio/pcm;rate=16000"}, end_of_turn=False)
                    audio_in_queue.task_done()
                    if chunk != b"\x00" * len(chunk):
                        self.notify_traffic()
                except asyncio.TimeoutError:
                    # Keep-Alive Silence Frame: Feed zero-PCM heartbeat on idle timeout to prevent WebSocket disconnection
                    try:
                        blob = types.Blob(data=silence_heartbeat, mime_type="audio/pcm;rate=16000")
                        if hasattr(session, "send_realtime_input"):
                            await session.send_realtime_input(audio=blob)
                        else:
                            await session.send(input={"data": silence_heartbeat, "mime_type": "audio/pcm;rate=16000"}, end_of_turn=False)
                    except Exception as hb_ex:
                        err_str = str(hb_ex).lower()
                        if "closed" in err_str or "disconnect" in err_str or "connection" in err_str:
                            if active_event.is_set():
                                logger.info(f"[Audio In Closed] Connection lost on heartbeat: {hb_ex}")
                                active_event.clear()
                            break
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as ex:
                    err_str = str(ex).lower()
                    if "closed" in err_str or "disconnect" in err_str or "connection" in err_str:
                        if active_event.is_set():
                            logger.info(f"[Audio In Closed] Connection lost: {ex}")
                            active_event.clear()
                        break
                    else:
                        logger.debug(f"[Audio In Notice] {ex}")
                        continue

        async def screen_vision_worker(session, active_event: asyncio.Event):
            from src.zero_ui.fovea_capture import FoveaCapturePipeline, Point, Rect
            from src.zero_ui.input_intent_detector import InputIntentDetector
            import queue
            import io
            from PIL import Image

            # Fovea Vision Pipeline — cursor-centric 1280×720 crop at 15–20 Hz
            # Frame interval derived from FOVEA_CAPTURE_HZ (default 15 Hz → 0.067 s).
            # Keep GEMINI_LIVE_FRAME_INTERVAL as a minimum floor to respect API rate limits.
            capture_hz = max(1, int(getattr(config, "FOVEA_CAPTURE_HZ", 15)))
            fovea_interval = 1.0 / capture_hz
            api_floor = float(getattr(config, "GEMINI_LIVE_FRAME_INTERVAL", 0.067))
            frame_interval = max(fovea_interval, api_floor)
            jpeg_quality = min(60, max(40, int(getattr(config, "GEMINI_LIVE_JPEG_QUALITY", 50))))
            move_threshold = int(getattr(config, "FOVEA_MOVE_THRESHOLD", 20))

            pipeline = FoveaCapturePipeline(
                fovea_width=1280,
                fovea_height=720,
                move_threshold=move_threshold,
            )
            
            detector = InputIntentDetector()
            intent_queue = queue.Queue()
            
            try:
                from pynput import mouse
                
                def on_move(x, y):
                    if detector.register_move(x, y):
                        detector.clear_buffer()
                        intent_queue.put({'type': 'cluster', 'point': (x, y)})

                def on_click(x, y, button, pressed):
                    if pressed:
                        detector.register_mouse_down(x, y)
                    else:
                        result = detector.register_mouse_up(x, y)
                        if result:
                            intent_queue.put(result)
                        else:
                            intent_queue.put(detector.register_click(x, y))

                mouse_listener = mouse.Listener(on_move=on_move, on_click=on_click)
                mouse_listener.daemon = True
                mouse_listener.start()
                self._mouse_listener = mouse_listener
            except ImportError:
                logger.debug("[FoveaCapture] pynput not installed, mouse intent detection disabled.")
                mouse_listener = None

            with mss.MSS() as sct:
                try:
                    while active_event.is_set() and not self._stop_event.is_set():
                        try:
                            has_intent = False
                            intent = None
                            try:
                                while not intent_queue.empty():
                                    intent = intent_queue.get_nowait()
                                    has_intent = True
                            except queue.Empty:
                                pass
                                
                            jpeg_bytes = None
                            if has_intent and intent:
                                try:
                                    if intent['type'] == 'drag':
                                        bbox = intent['bbox']
                                        grab_rect = {"left": int(bbox[0]), "top": int(bbox[1]), "width": int(bbox[2]-bbox[0]), "height": int(bbox[3]-bbox[1])}
                                        # Clamp slightly to ensure it doesn't crash on invalid boxes
                                        if grab_rect["width"] > 0 and grab_rect["height"] > 0:
                                            sct_img = sct.grab(grab_rect)
                                            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                                            buf = io.BytesIO()
                                            img.save(buf, format="JPEG", quality=jpeg_quality)
                                            jpeg_bytes = buf.getvalue()
                                    else:
                                        pt = intent['point']
                                        cursor_pt = Point(int(pt[0]), int(pt[1]))
                                        monitor = pipeline._find_monitor_for_cursor(sct, cursor_pt)
                                        clamped_bbox = pipeline.compute_clamped_bbox(cursor_pt, monitor)
                                        grab_rect = {"left": clamped_bbox.left, "top": clamped_bbox.top, "width": clamped_bbox.width, "height": clamped_bbox.height}
                                        sct_img = sct.grab(grab_rect)
                                        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                                        img = pipeline._draw_reticle(img, Point(cursor_pt.x - clamped_bbox.left, cursor_pt.y - clamped_bbox.top))
                                        buf = io.BytesIO()
                                        img.save(buf, format="JPEG", quality=jpeg_quality)
                                        jpeg_bytes = buf.getvalue()
                                except Exception as e:
                                    logger.debug(f"[FoveaCapture] Intent capture error: {e}")
                                    jpeg_bytes = None

                            if not jpeg_bytes:
                                # Standard debounce polling if no intent overrides it
                                jpeg_bytes = pipeline.capture_fovea_frame(sct, jpeg_quality=jpeg_quality)

                            if not jpeg_bytes:
                                await asyncio.sleep(frame_interval)
                                continue

                            blob = types.Blob(data=jpeg_bytes, mime_type="image/jpeg")

                            if hasattr(session, "send_realtime_input"):
                                await session.send_realtime_input(video=blob)
                            else:
                                await session.send(input={"data": jpeg_bytes, "mime_type": "image/jpeg"}, end_of_turn=False)
                            self.notify_traffic()

                        except asyncio.CancelledError:
                            break
                        except Exception as ex:
                            logger.debug(f"[Fovea Vision Notice] {ex}")
                            await asyncio.sleep(frame_interval)
                            continue

                        try:
                            await asyncio.sleep(frame_interval)
                        except asyncio.CancelledError:
                            break
                finally:
                    if mouse_listener:
                        try:
                            mouse_listener.stop()
                        except Exception:
                            pass
                    if getattr(self, "_mouse_listener", None) == mouse_listener:
                        self._mouse_listener = None

        async def audio_output_worker(out_stream, active_event: asyncio.Event):
            jitter_buf = bytearray()
            min_jitter_bytes = 5760  # 120ms at 24kHz 16-bit Mono (48000 B/s)
            is_buffering = True
            last_play_time = 0.0

            while active_event.is_set() and not self._stop_event.is_set():
                if barge_in_event.is_set() or turn_interrupted.is_set():
                    jitter_buf.clear()
                    is_buffering = True
                    _set_ai_speaking(False)
                    barge_in_event.clear()

                try:
                    audio_chunk = await asyncio.wait_for(audio_out_queue.get(), timeout=0.1)
                    if barge_in_event.is_set() or turn_interrupted.is_set():
                        jitter_buf.clear()
                        is_buffering = True
                        _set_ai_speaking(False)
                        barge_in_event.clear()
                        audio_out_queue.task_done()
                        continue

                    if audio_chunk:
                        jitter_buf.extend(audio_chunk)
                    audio_out_queue.task_done()

                    # Pre-buffer initial 120ms before outputting to prevent stuttering
                    if is_buffering:
                        if len(jitter_buf) >= min_jitter_bytes:
                            is_buffering = False

                    if not is_buffering or len(jitter_buf) >= min_jitter_bytes:
                        if turn_interrupted.is_set():
                            jitter_buf.clear()
                            _set_ai_speaking(False)
                            continue
                        chunk_to_play = bytes(jitter_buf)
                        jitter_buf.clear()
                        _set_ai_speaking(True)
                        last_play_time = time.perf_counter()
                        try:
                            await loop.run_in_executor(None, out_stream.write, chunk_to_play)
                        except Exception as write_err:
                            logger.debug(f"[Audio Out Write Notice] {write_err}")
                            try:
                                if hasattr(out_stream, "start") and not getattr(out_stream, "active", False):
                                    out_stream.start()
                            except Exception:
                                pass
                        finally:
                            last_play_time = time.perf_counter()

                except asyncio.TimeoutError:
                    if barge_in_event.is_set():
                        jitter_buf.clear()
                        is_buffering = True
                        _set_ai_speaking(False)
                        last_play_time = 0.0
                        barge_in_event.clear()
                        continue

                    # Flush remaining buffer on silence gap
                    if len(jitter_buf) > 0:
                        chunk_to_play = bytes(jitter_buf)
                        jitter_buf.clear()
                        _set_ai_speaking(True)
                        last_play_time = time.perf_counter()
                        try:
                            await loop.run_in_executor(None, out_stream.write, chunk_to_play)
                        except Exception as write_err:
                            logger.debug(f"[Audio Out Write Gap Notice] {write_err}")
                            try:
                                if hasattr(out_stream, "start") and not getattr(out_stream, "active", False):
                                    out_stream.start()
                            except Exception:
                                pass
                        finally:
                            last_play_time = time.perf_counter()
                    else:
                        # Post-Playback Grace Period (Hangover Time):
                        # When audio_out_queue is empty and playback finishes, keep zero-PCM
                        # mute guard active for an additional 300ms before unmuting raw mic stream.
                        if audio_out_queue.empty():
                            if last_play_time == 0.0 or (time.perf_counter() - last_play_time >= 0.30):
                                _set_ai_speaking(False)
                                last_play_time = 0.0
                    is_buffering = True
                    continue
                except asyncio.CancelledError:
                    _set_ai_speaking(False)
                    break
                except Exception as ex:
                    logger.debug(f"[Audio Out Notice] {ex}")
                    _set_ai_speaking(False)
                    await asyncio.sleep(0.05)
                    continue

            _set_ai_speaking(False)

        async def receive_worker(session, active_event: asyncio.Event):
            try:
                # Persistent multi-turn receive loop:
                # Google GenAI's session.receive() yields items for a single turn and terminates on turn_complete.
                # Looping over session.receive() keeps the underlying WebSocket alive across indefinite turns & barge-ins.
                while active_event.is_set() and not self._stop_event.is_set() and self._is_running:
                    turn_had_messages = False
                    async for response in session.receive():
                        turn_had_messages = True
                        if not active_event.is_set() or self._stop_event.is_set() or not self._is_running:
                            break

                        try:
                            server_content = getattr(response, "server_content", None)
                            if server_content is not None:
                                # Non-destructive Barge-in: flush active audio playback immediately without resetting socket connection
                                if getattr(server_content, "interrupted", False) is True:
                                    _trigger_barge_in("server_interrupted_flag")
                                    continue

                                # Turn Complete: Model finished current response turn - maintain live connection
                                if getattr(server_content, "turn_complete", False) is True:
                                    logger.debug("[LiveCopilot] Model turn complete. Stream active.")
                                    turn_interrupted.clear()
                                    continue

                                model_turn = getattr(server_content, "model_turn", None)
                                if model_turn:
                                    # Clear interruption lock on new model turn so response is never dropped
                                    turn_interrupted.clear()
                                    for part in model_turn.parts:
                                        # Suppress thinking process text / thoughts to guarantee zero-latency voice output
                                        if getattr(part, "thought", False) is True:
                                            continue
                                        if hasattr(part, "text") and part.text:
                                            print(part.text, end="", flush=True)
                                            with self._lock:
                                                if self._session_transcript and self._session_transcript[-1].get("role") == "model":
                                                    self._session_transcript[-1]["text"] += part.text
                                                else:
                                                    self._session_transcript.append({"role": "model", "text": part.text})

                                        if hasattr(part, "inline_data") and part.inline_data:
                                            audio_data = part.inline_data.data
                                            if audio_data and not turn_interrupted.is_set():
                                                self.notify_traffic()
                                                # Accept audio output when outside immediate barge-in recovery window (> 0.2s)
                                                if time.perf_counter() - self._last_barge_in_time > 0.20:
                                                    # Eager Playback Mute: immediately engage zero-PCM mute guard before queuing chunk
                                                    self._is_ai_speaking = True
                                                    self._is_speaking_active.set()
                                                    try:
                                                        audio_out_queue.put_nowait(audio_data)
                                                    except (asyncio.QueueFull, Exception):
                                                        try:
                                                            audio_out_queue.get_nowait()
                                                            audio_out_queue.task_done()
                                                        except Exception:
                                                            pass
                                                        try:
                                                            audio_out_queue.put_nowait(audio_data)
                                                        except Exception:
                                                            pass
                        except Exception as parse_err:
                            logger.debug(f"[Receive Worker Item Parse Notice] {parse_err}")
                            continue

                    if not turn_had_messages:
                        if self._stop_event.is_set() or not self._is_running or not active_event.is_set():
                            break
                        await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                pass
            except Exception as ex:
                close_code = getattr(ex, "code", getattr(ex, "close_code", None))
                close_reason = getattr(ex, "reason", getattr(ex, "close_reason", str(ex)))
                logger.warning(f"[Live Session Disconnected] Error={ex} (CloseCode={close_code}, Reason={close_reason})")
                print(f"\n[LiveCopilot Disconnected] {ex} (CloseCode={close_code}, Reason={close_reason})", flush=True)
            finally:
                if active_event.is_set():
                    active_event.clear()

        reconnect_delay = 1.0

        try:
            with mic_stream, speaker_stream:
                while not self._stop_event.is_set() and self._is_running:
                    session_active_event = asyncio.Event()
                    connected = False
                    _clear_queues()

                    for target_model in candidates:
                        if self._stop_event.is_set() or not self._is_running:
                            break
                        try:
                            logger.info(f"[LiveCopilot] Sending handshake to {target_model}...")
                            if hasattr(self, "on_handshake") and self.on_handshake:
                                try:
                                    self.on_handshake()
                                except Exception:
                                    pass
                            conn_ctx = client.aio.live.connect(model=target_model, config=live_config)
                            session = await asyncio.wait_for(conn_ctx.__aenter__(), timeout=5.0)
                            self._active_session = session
                            self._active_conn_ctx = conn_ctx
                            try:
                                logger.info(f"✅ [LiveCopilot Connected] Live session active on '{target_model}'")
                                self._is_connected = True
                                connected = True
                                self.set_traffic_state(False)
                                if hasattr(self, "on_connected") and self.on_connected:
                                    try:
                                        self.on_connected()
                                    except Exception:
                                        pass
                                _clear_queues()
                                barge_in_event.clear()
                                turn_interrupted.clear()
                                session_active_event.set()
                                reconnect_delay = 1.0

                                worker_tasks = [
                                    asyncio.create_task(audio_input_worker(session, session_active_event)),
                                    asyncio.create_task(screen_vision_worker(session, session_active_event)),
                                    asyncio.create_task(audio_output_worker(speaker_stream, session_active_event)),
                                    asyncio.create_task(receive_worker(session, session_active_event))
                                ]
                                self._worker_tasks = worker_tasks

                                # Non-destructive loop: WebSocket session stays alive until receive_worker finishes or user stops
                                while session_active_event.is_set() and not self._stop_event.is_set() and self._is_running:
                                    if worker_tasks[3].done():
                                        break
                                    await asyncio.sleep(0.05)

                                session_active_event.clear()
                                for task in worker_tasks:
                                    if not task.done():
                                        task.cancel()
                                results = await asyncio.gather(*worker_tasks, return_exceptions=True)
                                for res in results:
                                    if isinstance(res, (AssertionError, KeyboardInterrupt)):
                                        raise res
                                _clear_queues()
                                break
                            finally:
                                self._active_session = None
                                self._active_conn_ctx = None
                                try:
                                    await conn_ctx.__aexit__(None, None, None)
                                except Exception:
                                    pass
                        except asyncio.TimeoutError:
                            logger.warning(f"[LiveCopilot Handshake Timeout] {target_model} connection timed out after 5.0s")
                            if target_model != candidates[-1] and not self._stop_event.is_set() and self._is_running:
                                try:
                                    await asyncio.sleep(0.5)
                                except (asyncio.CancelledError, RuntimeError):
                                    break
                        except (KeyboardInterrupt, AssertionError):
                            raise
                        except Exception as ex:
                            err_msg = str(ex)
                            if "cannot schedule new futures after interpreter shutdown" in err_msg.lower() or "interpreter shutdown" in err_msg.lower():
                                logger.debug(f"[LiveCopilot Shutdown] Breaking loop on interpreter shutdown: {ex}")
                                self._is_running = False
                                self._stop_event.set()
                                break
                            logger.warning(f"[LiveCopilot Connection Attempt Failed] {target_model}: {ex}")
                            if target_model != candidates[-1] and not self._stop_event.is_set() and self._is_running:
                                try:
                                    await asyncio.sleep(0.5)
                                except (asyncio.CancelledError, RuntimeError):
                                    break

                    if not connected and not self._stop_event.is_set() and self._is_running:
                        if hasattr(self, "on_error") and self.on_error:
                            try:
                                self.on_error()
                            except Exception:
                                pass

                    self._is_connected = False
                    _clear_queues()
                    if self._stop_event.is_set() or not self._is_running:
                        break

                    if not connected:
                        logger.warning(f"[LiveCopilot] Disconnected. Reconnecting in {reconnect_delay:.1f}s...")
                    else:
                        logger.info(f"[LiveCopilot] Session ended. Auto-reconnecting in {reconnect_delay:.1f}s...")

                    try:
                        sleep_steps = max(1, int(reconnect_delay * 10))
                        for _ in range(sleep_steps):
                            if self._stop_event.is_set() or not self._is_running:
                                break
                            await asyncio.sleep(0.1)
                    except (asyncio.CancelledError, RuntimeError):
                        break
                    reconnect_delay = min(10.0, reconnect_delay * 1.5)
        finally:
            self._is_running = False
            self._is_connected = False
            self._is_ai_speaking = False
            for s in (mic_stream, speaker_stream):
                try:
                    if hasattr(s, "abort"):
                        s.abort()
                except Exception:
                    pass
                try:
                    s.stop()
                except Exception:
                    pass
                try:
                    s.close()
                except Exception:
                    pass
            self._mic_stream = None
            self._speaker_stream = None


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    session = LiveCopilotSession()
    try:
        asyncio.run(session.run())
    except KeyboardInterrupt:
        logger.info("[LiveCopilot] Session stopped by user (KeyboardInterrupt).")
