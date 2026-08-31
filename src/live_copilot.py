import asyncio
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
from src.live_memory import LiveSessionMemory

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
    Pumps cv2.waitKey(1) multiple times to flush the Win32 message queue (WM_DESTROY / WM_NCDESTROY)
    and guarantee the window is fully disposed without hanging the process or blocking GUI threads.
    """
    try:
        if win_name:
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
        self.noise_threshold = getattr(config, "GEMINI_LIVE_RMS_THRESHOLD", 2500.0)
        
        self._is_running = False
        self._is_connected = False
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._client: Optional[genai.Client] = None
        self._backend_desc: Optional[str] = None
        self._is_ai_speaking = False
        self._last_ai_speech_end_time = 0.0
        self._last_barge_in_time = 0.0
        self._mic_stream = None
        self._speaker_stream = None
        self.memory = LiveSessionMemory()
        self._session_transcript: list[dict[str, Any]] = []
        self.on_audio_level = None

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

    def warmup(self):
        """Zero-delay no-op: background warmup removed to guarantee instant direct connection."""
        pass

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
            sound_feedback(880, 100) # High pitch chime on start

            self._worker_thread = threading.Thread(
                target=self._run_thread,
                name="LiveCopilotWorker",
                daemon=True
            )
            self._worker_thread.start()
            return True

    def stop(self) -> bool:
        """Stops the active live co-pilot session gracefully and signals workers to release audio devices."""
        with self._lock:
            was_running = self._is_running or (self._worker_thread is not None and self._worker_thread.is_alive())
            if not was_running and not self._session_transcript:
                return False

            logger.info("[LiveCopilot] Stopping Multimodal Live Session...")
            self._is_running = False
            self._is_connected = False
            self._is_ai_speaking = False
            self._stop_event.set()
            try:
                sound_feedback(440, 100) # Low pitch chime on stop
            except Exception:
                pass

            thread_to_join = self._worker_thread
            self._worker_thread = None

        if self.show_preview:
            _safe_destroy_cv2_windows("Gemini Vision Stream")

        if thread_to_join and thread_to_join.is_alive() and threading.current_thread() != thread_to_join:
            thread_to_join.join(timeout=0.3)

        self._flush_memory_snapshot()
        logger.info("[LiveCopilot] Session stopped cleanly.")
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
        try:
            asyncio.run(self._async_live_loop())
        except Exception as e:
            logger.error(f"[LiveCopilot Worker Error] {e}", exc_info=True)
        finally:
            self._is_running = False
            self._is_connected = False
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
        system_instruction_text = (
            "You are an expert real-time AI co-pilot assisting the user with their active screen and workflow. "
            "You can see their screen and hear their voice simultaneously in real time. "
            "Always respond naturally, fluently, and conversationally in Thai (using standard English technical terms when appropriate). "
            "Deliver well-structured spoken answers with genuine context and conversational depth—typically in 2 to 4 clear, informative sentences. "
            "Never give one-word or overly robotic responses, but do not waffle or include filler phrases. "
            "Proactively reference relevant context visible on their screen (e.g. code, terminal outputs, error traces, web pages, or active windows) to give immediate, actionable insight."
        )
        if rolling_context:
            system_instruction_text += f"\n\n[Previous Session Context / Rolling Memory]:\n{rolling_context}"
            logger.info(f"[LiveMemory] Hydrated system instruction with previous session context.")

        live_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
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

        def _safe_put_audio_in(data: bytes):
            """Threadsafe non-blocking audio enqueue with QueueFull overflow protection."""
            try:
                audio_in_queue.put_nowait(data)
            except (asyncio.QueueFull, Exception):
                # Queue full (e.g. during WebSocket reconnecting) -> drop oldest chunk to stay real-time
                try:
                    audio_in_queue.get_nowait()
                    audio_in_queue.task_done()
                except Exception:
                    pass
                try:
                    audio_in_queue.put_nowait(data)
                except Exception:
                    pass

        noise_threshold = getattr(self, "noise_threshold", getattr(config, "GEMINI_LIVE_RMS_THRESHOLD", 2500.0))
        min_speech_frames = max(2, int(getattr(config, "GEMINI_LIVE_MIN_SPEECH_FRAMES", 3)))  # ~192ms (3 * 64ms)
        speech_hangover_frames = 6  # ~384ms hangover window (6 * 64ms)
        active_speech_counter = 0
        hangover_counter = 0
        pre_speech_buffer: list[bytes] = []

        def mic_callback(indata, frames, time_info, status):
            nonlocal active_speech_counter, hangover_counter, pre_speech_buffer
            if not self._stop_event.is_set() and self._is_running:
                try:
                    raw_bytes = bytes(indata)

                    # AEC / Mute Guard: While AI is speaking (or within 200ms room echo reverberation window),
                    # completely disable microphone barge-in and stream comfort silence (0-PCM) to eliminate acoustic feedback.
                    now_ts = time.perf_counter()
                    if self._is_ai_speaking or (now_ts - getattr(self, "_last_ai_speech_end_time", 0.0) < 0.20):
                        active_speech_counter = 0
                        pre_speech_buffer.clear()
                        silence_bytes = b"\x00" * len(raw_bytes)
                        loop.call_soon_threadsafe(_safe_put_audio_in, silence_bytes)
                        return

                    int16_arr = np.frombuffer(raw_bytes, dtype=np.int16)
                    rms = float(np.sqrt(np.mean(int16_arr.astype(np.float32)**2))) if len(int16_arr) > 0 else 0.0
                    current_threshold = float(getattr(self, "noise_threshold", getattr(config, "GEMINI_LIVE_RMS_THRESHOLD", 2500.0)))

                    if hasattr(self, "on_audio_level") and self.on_audio_level:
                        try:
                            self.on_audio_level(rms)
                        except Exception:
                            pass

                    if rms >= current_threshold:
                        active_speech_counter += 1
                        if active_speech_counter < min_speech_frames:
                            # Tentative speech onset: buffer chunk and send comfort silence to prevent false barge-in trigger
                            pre_speech_buffer.append(raw_bytes)
                            if len(pre_speech_buffer) > min_speech_frames:
                                pre_speech_buffer.pop(0)
                            silence_bytes = b"\x00" * len(raw_bytes)
                            loop.call_soon_threadsafe(_safe_put_audio_in, silence_bytes)
                        elif active_speech_counter == min_speech_frames:
                            # Speech confirmed (sustained >= min_speech_frames e.g. 192ms)!
                            # Flush all buffered pre-speech frames to avoid clipping the start of speech
                            for pre_chunk in pre_speech_buffer:
                                loop.call_soon_threadsafe(_safe_put_audio_in, pre_chunk)
                            pre_speech_buffer.clear()
                            loop.call_soon_threadsafe(_safe_put_audio_in, raw_bytes)
                            hangover_counter = speech_hangover_frames
                        else:
                            # Sustained active speech
                            loop.call_soon_threadsafe(_safe_put_audio_in, raw_bytes)
                            hangover_counter = speech_hangover_frames
                    else:
                        if active_speech_counter < min_speech_frames:
                            # Isolated noise spike / wind pop that didn't reach min_speech_frames -> discard buffer
                            pre_speech_buffer.clear()
                            active_speech_counter = 0
                            silence_bytes = b"\x00" * len(raw_bytes)
                            loop.call_soon_threadsafe(_safe_put_audio_in, silence_bytes)
                        else:
                            # User paused or finished speaking
                            active_speech_counter = max(0, active_speech_counter - 1)
                            if hangover_counter > 0:
                                hangover_counter -= 1
                                loop.call_soon_threadsafe(_safe_put_audio_in, raw_bytes)
                            else:
                                pre_speech_buffer.clear()
                                silence_bytes = b"\x00" * len(raw_bytes)
                                loop.call_soon_threadsafe(_safe_put_audio_in, silence_bytes)
                except Exception:
                    pass

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

        def _set_ai_speaking(speaking: bool):
            if self._is_ai_speaking and not speaking:
                self._last_ai_speech_end_time = time.perf_counter()
            self._is_ai_speaking = speaking

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
            with mss.MSS() as sct:
                monitors = sct.monitors
                # Strict 1 FPS cap (interval >= 1.0s) and 720p max resolution to prevent bandwidth throttling
                frame_interval = max(1.0, float(getattr(config, "GEMINI_LIVE_FRAME_INTERVAL", 1.0)))
                jpeg_quality = min(60, max(40, int(getattr(config, "GEMINI_LIVE_JPEG_QUALITY", 50))))
                win_name = "Gemini Vision Stream"
                try:
                    while active_event.is_set() and not self._stop_event.is_set():
                        try:
                            # Dynamic monitor index lookup at runtime (Default: Monitor 3)
                            cur_mon = self.target_monitor if self.target_monitor is not None else getattr(config, "GEMINI_LIVE_TARGET_MONITOR", 3)
                            try:
                                num_mon = len(monitors)
                            except (TypeError, Exception):
                                num_mon = 1

                            if cur_mon >= num_mon or cur_mon < 1:
                                target_mon_idx = 3 if num_mon > 3 else (1 if num_mon > 1 else 0)
                            else:
                                target_mon_idx = cur_mon

                            target_rect = monitors[target_mon_idx] if hasattr(monitors, "__getitem__") else {"top": 0, "left": 0, "width": 1920, "height": 1080}
                            sct_img = sct.grab(target_rect)
                            if not sct_img or not hasattr(sct_img, "width") or not isinstance(getattr(sct_img, "width", None), int) or sct_img.width <= 0 or not hasattr(sct_img, "bgra") or not isinstance(sct_img.bgra, bytes):
                                await asyncio.sleep(0.5)
                                continue

                            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                            # Strict 720p cap (max width 1280, max height 720) to prevent bandwidth saturation
                            if img.width > 1280 or img.height > 720:
                                img.thumbnail((1280, 720), Image.Resampling.BILINEAR)

                            buf = io.BytesIO()
                            img.save(buf, format="JPEG", quality=jpeg_quality)
                            jpeg_bytes = buf.getvalue()
                            blob = types.Blob(data=jpeg_bytes, mime_type="image/jpeg")

                            if hasattr(session, "send_realtime_input"):
                                await session.send_realtime_input(video=blob)
                            else:
                                await session.send(input={"data": jpeg_bytes, "mime_type": "image/jpeg"}, end_of_turn=False)

                            # Floating Screen Vision Preview Window (HUD)
                            if self.show_preview:
                                try:
                                    cv_frame = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
                                    preview_img = cv2.resize(cv_frame, (360, 202), interpolation=cv2.INTER_AREA)

                                    # Draw HUD Header Banner
                                    cv2.rectangle(preview_img, (0, 0), (360, 24), (20, 20, 20), -1)
                                    cv2.circle(preview_img, (12, 12), 4, (0, 255, 0), -1)  # Green live dot
                                    cv2.putText(
                                        preview_img,
                                        f"LIVE [Monitor {target_mon_idx}]",
                                        (22, 16),
                                        cv2.FONT_HERSHEY_SIMPLEX,
                                        0.40,
                                        (0, 255, 0),
                                        1,
                                        cv2.LINE_AA
                                    )
                                    fps_val = 1.0 / max(0.1, frame_interval)
                                    cv2.putText(
                                        preview_img,
                                        f"{img.width}x{img.height} {fps_val:.2f}FPS",
                                        (210, 16),
                                        cv2.FONT_HERSHEY_SIMPLEX,
                                        0.35,
                                        (180, 180, 180),
                                        1,
                                        cv2.LINE_AA
                                    )

                                    cv2.namedWindow(win_name, cv2.WINDOW_AUTOSIZE | cv2.WINDOW_KEEPRATIO)
                                    cv2.setWindowProperty(win_name, cv2.WND_PROP_TOPMOST, 1)
                                    cv2.imshow(win_name, preview_img)
                                    cv2.waitKey(1)
                                except Exception as hud_err:
                                    logger.debug(f"[Vision Preview HUD Notice]: {hud_err}")

                        except asyncio.CancelledError:
                            break
                        except Exception as ex:
                            logger.debug(f"[Vision Grab Notice] {ex}")
                            await asyncio.sleep(frame_interval)
                            continue

                        try:
                            await asyncio.sleep(frame_interval)
                        except asyncio.CancelledError:
                            break
                finally:
                    if self.show_preview:
                        _safe_destroy_cv2_windows("Gemini Vision Stream")

        async def audio_output_worker(out_stream, active_event: asyncio.Event):
            jitter_buf = bytearray()
            min_jitter_bytes = 5760  # 120ms at 24kHz 16-bit Mono (48000 B/s)
            is_buffering = True
            last_play_time = 0.0

            while active_event.is_set() and not self._stop_event.is_set():
                if barge_in_event.is_set():
                    jitter_buf.clear()
                    is_buffering = True
                    _set_ai_speaking(False)
                    barge_in_event.clear()

                try:
                    audio_chunk = await asyncio.wait_for(audio_out_queue.get(), timeout=0.1)
                    if barge_in_event.is_set():
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
                        chunk_to_play = bytes(jitter_buf)
                        jitter_buf.clear()
                        _set_ai_speaking(True)
                        last_play_time = time.perf_counter()
                        try:
                            await loop.run_in_executor(None, out_stream.write, chunk_to_play)
                        finally:
                            if audio_out_queue.empty() and len(jitter_buf) == 0:
                                _set_ai_speaking(False)

                except asyncio.TimeoutError:
                    if barge_in_event.is_set():
                        jitter_buf.clear()
                        is_buffering = True
                        _set_ai_speaking(False)
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
                        finally:
                            if audio_out_queue.empty() and len(jitter_buf) == 0:
                                _set_ai_speaking(False)
                    else:
                        if audio_out_queue.empty() and (time.perf_counter() - last_play_time > 0.05):
                            _set_ai_speaking(False)
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
                                    now = time.perf_counter()
                                    is_active = self._is_ai_speaking or not audio_out_queue.empty()
                                    if is_active and (now - self._last_barge_in_time > 1.0):
                                        self._last_barge_in_time = now
                                        self._is_ai_speaking = False
                                        _flush_audio_out()
                                        barge_in_event.set()
                                        print("\n[⚡ Barge-in Triggered] Interrupted by user speech!", flush=True)
                                        with self._lock:
                                            self._session_transcript.append({"role": "user", "text": "[User interrupted model speech / Barge-in]"})
                                    else:
                                        self._is_ai_speaking = False
                                        _flush_audio_out()
                                        barge_in_event.set()
                                    continue

                                # Turn Complete: Model finished current response turn - maintain live connection
                                if getattr(server_content, "turn_complete", False) is True:
                                    logger.debug("[LiveCopilot] Model turn complete. Stream active.")
                                    continue

                                model_turn = getattr(server_content, "model_turn", None)
                                if model_turn:
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
                                            if audio_data:
                                                # Accept audio output when outside immediate barge-in recovery window (> 0.4s)
                                                if time.perf_counter() - self._last_barge_in_time > 0.4:
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
                            conn_ctx = client.aio.live.connect(model=target_model, config=live_config)
                            session = await asyncio.wait_for(conn_ctx.__aenter__(), timeout=5.0)
                            try:
                                logger.info(f"✅ [LiveCopilot Connected] Live session active on '{target_model}'")
                                self._is_connected = True
                                connected = True
                                _clear_queues()
                                barge_in_event.clear()
                                session_active_event.set()
                                reconnect_delay = 1.0

                                worker_tasks = [
                                    asyncio.create_task(audio_input_worker(session, session_active_event)),
                                    asyncio.create_task(screen_vision_worker(session, session_active_event)),
                                    asyncio.create_task(audio_output_worker(speaker_stream, session_active_event)),
                                    asyncio.create_task(receive_worker(session, session_active_event))
                                ]

                                # Non-destructive loop: WebSocket session stays alive until receive_worker finishes or user stops
                                while session_active_event.is_set() and not self._stop_event.is_set() and self._is_running:
                                    if worker_tasks[3].done():
                                        break
                                    await asyncio.sleep(0.05)

                                session_active_event.clear()
                                for task in worker_tasks:
                                    if not task.done():
                                        task.cancel()
                                await asyncio.gather(*worker_tasks, return_exceptions=True)
                                _clear_queues()
                                break
                            finally:
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
                        except KeyboardInterrupt:
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
            try:
                mic_stream.stop()
                mic_stream.close()
            except Exception:
                pass
            try:
                speaker_stream.stop()
                speaker_stream.close()
            except Exception:
                pass
            self._mic_stream = None
            self._speaker_stream = None
