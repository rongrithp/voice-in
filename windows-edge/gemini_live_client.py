#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows Edge: Gemini Live Multimodal Client & Context Fusion Pipeline
=============================================================================
End-to-end multimodal pipeline integrating:
1. Dual Wake Trigger: Hotkey F20 or voice wake phrase ("เจมิไนมาช่วยหน่อย")
2. Sensory Ingestion: Cursor-anchored zero-disk window capture (visual_cortex)
3. Context Fusion: [Previous Session Context] + [Window Image] + [Voice Intention]
4. Audio Output: Low-latency 24kHz audio playback via sounddevice RawOutputStream
5. Kill-Switch: F20 aborts stream/playback immediately and returns to STANDBY
=============================================================================
"""

import sys
import os
import io
import time
import asyncio
import logging
import argparse
import threading
import queue
import traceback
from typing import Optional, Dict, Any, List, Callable, Tuple

# Ensure UTF-8 console output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(MODULE_DIR)

if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from dotenv import load_dotenv
# Load .env from root workspace or local directory
load_dotenv(os.path.join(ROOT_DIR, ".env"))
load_dotenv(os.path.join(MODULE_DIR, ".env"))

import sounddevice as sd
from google import genai
from google.genai import types

from visual_cortex import look_at_cursor, capture_window_at_cursor
from live_copilot_fsm import CopilotState, LiveCopilotFSM

import re

logger = logging.getLogger("gemini_live_client")

TARGET_PATTERN = re.compile(
    r"<<TARGET:\s*([^>]+)>>",
    re.IGNORECASE
)


def parse_target_coordinates(
    text: str,
    window_metadata: Optional[Dict[str, Any]] = None
) -> Tuple[str, Optional[Tuple[float, float, float, float]]]:
    """
    Parses <<TARGET: [...]>> from response text,
    maps normalized (0-1000) coordinates to screen pixel coordinates,
    and returns (clean_text, (screen_x, screen_y, width, height)).
    """
    if not text:
        return text, None

    m = TARGET_PATTERN.search(text)
    if not m:
        return text, None

    try:
        raw_inner = m.group(1)
        nums = [int(x) for x in re.findall(r"\d+", raw_inner)]
        if len(nums) < 4:
            return TARGET_PATTERN.sub("", text).strip(), None

        c0, c1, c2, c3 = nums[0], nums[1], nums[2], nums[3]
        ymin = min(c0, c2)
        ymax = max(c0, c2)
        xmin = min(c1, c3)
        xmax = max(c1, c3)

        clean_text = TARGET_PATTERN.sub("", text).strip()

        win_rect = (window_metadata or {}).get("window_rect", {})
        win_left = win_rect.get("left", 0)
        win_top = win_rect.get("top", 0)
        win_w = (window_metadata or {}).get("dimensions", {}).get("width", 1920)
        win_h = (window_metadata or {}).get("dimensions", {}).get("height", 1080)

        box_x = float(win_left + (xmin / 1000.0) * win_w)
        box_y = float(win_top + (ymin / 1000.0) * win_h)
        box_w = float(max(20.0, ((xmax - xmin) / 1000.0) * win_w))
        box_h = float(max(20.0, ((ymax - ymin) / 1000.0) * win_h))

        return clean_text, (box_x, box_y, box_w, box_h)
    except Exception as e:
        logger.debug(f"[GeminiLiveClient] Error parsing target coordinates: {e}")
        return text, None


GREETING_WAV_PATH = os.path.join(MODULE_DIR, "assets", "greeting.wav")
GOODBYE_WAV_PATH = os.path.join(MODULE_DIR, "assets", "goodbye.wav")


def _load_asset_pcm(path: str) -> bytes:
    try:
        import wave
        if os.path.exists(path):
            with wave.open(path, "rb") as wf:
                return wf.readframes(wf.getnframes())
    except Exception:
        pass
    return b""


_GREETING_PCM = _load_asset_pcm(GREETING_WAV_PATH)
_GOODBYE_PCM = _load_asset_pcm(GOODBYE_WAV_PATH)


class SessionContextMemory:
    """
    Rolling conversational memory buffer.
    Maintains dialogue history and window context snapshots across session turns.
    """

    def __init__(self, max_turns: int = 5):
        self.max_turns = max_turns
        self.turns: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def add_turn(
        self,
        user_query: str,
        model_reply: str,
        window_title: str = "",
        process_name: str = ""
    ):
        with self._lock:
            self.turns.append({
                "timestamp": time.time(),
                "user": user_query.strip(),
                "model": model_reply.strip(),
                "window_title": window_title.strip(),
                "process_name": process_name.strip()
            })
            if len(self.turns) > self.max_turns:
                self.turns.pop(0)

    def format_history_for_fusion(self) -> str:
        """Serializes previous dialogue turns into a compact context fusion header."""
        with self._lock:
            if not self.turns:
                return "ไม่มีประวัติก่อนหน้านี้ (First Turn)"

            lines = ["=== PREVIOUS CONVERSATION CONTEXT ==="]
            for i, turn in enumerate(self.turns[-3:], 1):
                win_tag = f" [{turn['process_name']} - {turn['window_title']}]" if turn.get("process_name") else ""
                lines.append(f"Turn {i}{win_tag}:")
                lines.append(f"  User:  \"{turn['user']}\"")
                lines.append(f"  Model: \"{turn['model']}\"")
            lines.append("======================================")
            return "\n".join(lines)

    def clear(self):
        with self._lock:
            self.turns.clear()


class GeminiLiveClient:
    """
    Client controller for Google Gemini Live Multimodal WebSocket API.
    Fuses screenshot context, session memory, and voice query into a single turn,
    then streams the model's audio response in real time to default speakers.
    """

    DEFAULT_MODELS = [
        "gemini-3.1-flash-live-preview",
        "gemini-2.0-flash-exp",
        "gemini-2.5-flash-native-audio-latest"
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        voice_name: str = "Aoede"
    ):
        self.api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or ""
        ).strip().strip('"').strip("'")

        env_model = os.getenv("GEMINI_LIVE_MODEL")
        if env_model:
            self.model_name = env_model.strip().strip('"').strip("'")
        elif model_name:
            self.model_name = model_name
        else:
            self.model_name = self.DEFAULT_MODELS[0]
        self.voice_name = voice_name
        self.memory = SessionContextMemory(max_turns=5)

        self._client: Optional[genai.Client] = None
        self._is_active = threading.Event()
        self._is_speaking = threading.Event()
        self._abort_requested = threading.Event()

        self._speaker_stream: Optional[sd.RawOutputStream] = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._active_session = None

    def reset_session(self):
        """Prepares client for a fresh session after an abort or disconnect."""
        self._abort_requested.clear()
        self._is_active.clear()
        self._is_speaking.clear()
        self._active_session = None


    @property
    def is_running(self) -> bool:
        return self._is_active.is_set()

    def _ensure_speaker_stream(self) -> sd.RawOutputStream:
        """Initializes or restarts 24kHz 16-bit Mono output stream for Gemini audio."""
        if self._speaker_stream is None or not getattr(self._speaker_stream, "active", False):
            try:
                if self._speaker_stream is not None:
                    try:
                        self._speaker_stream.close()
                    except Exception:
                        pass
                self._speaker_stream = sd.RawOutputStream(
                    samplerate=24000,
                    channels=1,
                    dtype="int16",
                    blocksize=2048
                )
                self._speaker_stream.start()
            except Exception as e:
                logger.error(f"[GeminiLiveClient] Speaker stream initialization failed: {e}")
                raise
        return self._speaker_stream

    def interrupt(self):
        """
        Hardware Barge-in Interruption Entrypoint:
        Instantly stops audio output (sd.stop() / stream abort) and signals
        the session to drop pending inbound audio chunks, transitioning back to LISTENING.
        """
        logger.info("[GeminiLiveClient: Barge-in Interrupt] Instantly stopping speaker output and dropping inbound chunks...")
        self._abort_requested.set()
        self._is_speaking.clear()

        # Stop global sounddevice immediately (< 50ms)
        try:
            sd.stop()
        except Exception as e:
            logger.debug(f"[GeminiLiveClient] sd.stop notice: {e}")

        # Abort speaker stream buffer
        if self._speaker_stream is not None:
            try:
                if hasattr(self._speaker_stream, "abort"):
                    self._speaker_stream.abort()
                elif hasattr(self._speaker_stream, "stop"):
                    self._speaker_stream.stop()
            except Exception as e:
                logger.debug(f"[GeminiLiveClient] Speaker stream abort notice: {e}")

    def abort(self):
        """
        Kill-Switch Entrypoint:
        Aborts active playback, cancels ongoing WebSocket request, and returns to STANDBY.
        """
        logger.info("[GeminiLiveClient: Kill-Switch] Aborting active streaming session...")
        self._abort_requested.set()
        self._is_active.clear()
        self._is_speaking.clear()

        if self._active_session is not None:
            try:
                if hasattr(self._active_session, "stop"):
                    self._active_session.stop()
                elif hasattr(self._active_session, "abort"):
                    self._active_session.abort()
            except Exception:
                pass
            self._active_session = None

        try:
            sd.stop()
        except Exception:
            pass

        # Stop speaker stream immediately to cut off sound
        if self._speaker_stream is not None:
            try:
                if hasattr(self._speaker_stream, "abort"):
                    self._speaker_stream.abort()
                elif hasattr(self._speaker_stream, "stop"):
                    self._speaker_stream.stop()
                self._speaker_stream.close()
            except Exception:
                pass
            self._speaker_stream = None

    def start_full_duplex_session(
        self,
        on_speaking_start: Optional[Callable[[], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
        on_turn_complete: Optional[Callable[[], None]] = None
    ) -> "FullDuplexLiveSession":
        """Starts a pure full-duplex live session with 1 FPS vision and continuous mic streaming."""
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set in environment or .env file.")
        if self._active_session is not None and getattr(self._active_session, "is_active", False):
            try:
                self._active_session.stop()
            except Exception:
                pass

        session = FullDuplexLiveSession(
            api_key=self.api_key,
            model_name=self.model_name,
            voice_name=self.voice_name,
            on_speaking_start=on_speaking_start,
            on_barge_in=on_barge_in,
            on_turn_complete=on_turn_complete
        )
        session.start()
        self._active_session = session
        self._is_active.set()
        return session

    def play_greeting(self) -> float:
        """Plays zero-latency natural audio greeting: 'มีอะไรให้ช่วยคะ'."""
        if _GREETING_PCM:
            try:
                stream = self._ensure_speaker_stream()
                stream.write(_GREETING_PCM)
                return len(_GREETING_PCM) / (24000 * 2)
            except Exception as e:
                logger.debug(f"[GeminiLiveClient] Greeting audio playback notice: {e}")
        return 0.0

    def play_goodbye(self) -> float:
        """Plays zero-latency natural audio goodbye: 'ไว้คุยกันใหม่นะคะ'."""
        if _GOODBYE_PCM:
            try:
                stream = self._ensure_speaker_stream()
                stream.write(_GOODBYE_PCM)
                return len(_GOODBYE_PCM) / (24000 * 2)
            except Exception as e:
                logger.debug(f"[GeminiLiveClient] Goodbye audio playback notice: {e}")
        return 0.0

    async def execute_turn_async(
        self,
        user_query: Optional[str] = None,
        audio_pcm: Optional[bytes] = None,
        image_bytes: Optional[bytes] = None,
        window_metadata: Optional[Dict[str, Any]] = None,
        play_audio: bool = True,
        on_text_token: Optional[Callable[[str], None]] = None,
        on_audio_data: Optional[Callable[[bytes], None]] = None,
        on_speaking_start: Optional[Callable[[], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes a single multimodal turn with context fusion:
        1. Packs [Previous Context] + [Window Context & Image] + [Native Audio Stream / Query].
        2. Streams captured microphone audio bytes directly to Gemini Live API.
        3. Plays 24kHz audio chunks in real time through the speakers.
        """
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not set in environment or .env file.")

        if self._client is None:
            self._client = genai.Client(
                api_key=self.api_key,
                http_options=types.HttpOptions(api_version="v1alpha")
            )

        self._abort_requested.clear()
        self._is_active.set()

        speaker_stream = self._ensure_speaker_stream() if play_audio else None

        # -------------------------------------------------------------
        # 1. Context Fusion Assembly
        # -------------------------------------------------------------
        win_title = (window_metadata or {}).get("title", "Active Desktop Window")
        win_proc = (window_metadata or {}).get("process_name", "explorer.exe")
        win_dims = (window_metadata or {}).get("dimensions", {})
        win_w = win_dims.get("width", 0)
        win_h = win_dims.get("height", 0)
        cursor_rel = (window_metadata or {}).get("cursor_rel_pos", {})
        cur_x = cursor_rel.get("x", 0)
        cur_y = cursor_rel.get("y", 0)

        history_summary = self.memory.format_history_for_fusion()

        fused_prompt = (
            f"{history_summary}\n\n"
            f"[CURRENT WINDOW SENSORY INGESTION]:\n"
            f"- Title: \"{win_title}\"\n"
            f"- Process: {win_proc} ({win_w}x{win_h} px)\n"
            f"- Cursor Position in Window: ({cur_x}, {cur_y})\n\n"
            f"[INSTRUCTION]:\n"
            f"ผู้ใช้กำลังพูดผ่านไมโครโฟนเป็นภาษาไทยสดๆ (Native Audio Stream แนบมาด้วย)\n"
            f"จงฟังเสียงคำพูดของผู้ใช้โดยตรง ตอบเป็นภาษาไทยด้วยเสียงพูดที่กระชับ เป็นธรรมชาติ เข้าใจง่าย ตรงประเด็น (ตอบสั้นๆ ไม่ยืดเยื้อ)\n"
            f"Ground all visual responses strictly on the LATEST image frame received in this turn; ignore visual elements from prior frames.\n"
            f"When referencing buttons, menus, shortcuts, or UI elements on the captured screen, append normalized coordinates formatted as: <<TARGET: [ymin, xmax] xmin, ymax,>> along with your spoken response."
        )
        if user_query:
            fused_prompt += f"\n(ข้อความเสียงอ้างอิง: \"{user_query}\")"

        parts: List[types.Part] = []

        # Attach image if provided, or capture live on the fly if missing
        if (image_bytes is None or len(image_bytes) == 0) and not window_metadata:
            try:
                live_snap = look_at_cursor()
                image_bytes = live_snap.get("image_bytes")
                window_metadata = live_snap
                win_dims = window_metadata.get("dimensions", {})
                win_w = win_dims.get("width", win_w)
                win_h = win_dims.get("height", win_h)
            except Exception as snap_e:
                logger.debug(f"[GeminiLiveClient] Live snapshot capture error: {snap_e}")

        if image_bytes and len(image_bytes) > 0:
            parts.append(types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"))

        # Attach fused context and query
        parts.append(types.Part.from_text(text=fused_prompt))

        live_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice_name)
                )
            ),
            thinking_config=types.ThinkingConfig(
                thinking_budget=0
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                activity_handling=types.ActivityHandling.NO_INTERRUPTION
            ),
            system_instruction=types.Content(
                parts=[types.Part.from_text(
                    text="คุณคือ Gemini Live Multimodal AI Voice Copilot ประจำคอมพิวเตอร์ ทำหน้าที่เป็นผู้ช่วยอัจฉริยะ ตอบกลับเป็นภาษาไทยด้วยเสียงที่กระชับ ตรงจุด และรวดเร็ว "
                         "Ground all visual responses strictly on the LATEST image frame received in this turn; ignore visual elements from prior frames. "
                         "When referencing buttons, menus, shortcuts, or UI elements on the captured screen, append normalized coordinates formatted as: <<TARGET: [ymin, xmax] xmin, ymax,>> along with your spoken response."
                )]
            )
        )

        accumulated_text: List[str] = []
        accumulated_output_transcripts: List[str] = []
        accumulated_user_transcripts: List[str] = []
        total_audio_bytes = 0
        audio_chunks_count = 0
        t_start = time.perf_counter()

        try:
            async with self._client.aio.live.connect(model=self.model_name, config=live_config) as session:
                self._active_session = session

                audio_len = len(audio_pcm) if audio_pcm else 0
                img_len = len(image_bytes) if image_bytes else 0
                disp_w = win_w if win_w else 1920
                disp_h = win_h if win_h else 1080
                print(f"[GeminiLive] Dispatching turn: Audio ({audio_len} bytes) + Screen ({img_len} bytes, {disp_w}x{disp_h}).")

                if audio_pcm and len(audio_pcm) > 0:
                    # 1. Send context (window image + instructions/history) with turn_complete=False
                    await session.send_client_content(
                        turns=[types.Content(parts=parts)],
                        turn_complete=False
                    )
                    # 2. Stream raw microphone audio bytes directly to Gemini Live
                    chunk_size = 3200  # 100ms at 16kHz 16-bit Mono PCM
                    for offset in range(0, len(audio_pcm), chunk_size):
                        chunk = audio_pcm[offset:offset + chunk_size]
                        await session.send_realtime_input(
                            media=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                        )
                        await asyncio.sleep(0.002)

                    # 3. Signal end of audio stream
                    await session.send_realtime_input(audio_stream_end=True)
                else:
                    await session.send_client_content(
                        turns=[types.Content(parts=parts)],
                        turn_complete=True
                    )

                msg_idx = 0
                async for resp in session.receive():
                    msg_idx += 1
                    if self._abort_requested.is_set():
                        logger.info("[GeminiLiveClient] Abort flag detected -> Terminating receive loop.")
                        break

                    server_content = getattr(resp, "server_content", None)
                    if server_content is not None:
                        # Log and collect transcriptions
                        if getattr(server_content, "input_transcription", None) and server_content.input_transcription.text:
                            in_txt = server_content.input_transcription.text
                            accumulated_user_transcripts.append(in_txt)
                            logger.debug(f"[GeminiLive Rx #{msg_idx}] input_transcription: \"{in_txt}\"")

                        if getattr(server_content, "output_transcription", None) and server_content.output_transcription.text:
                            out_txt = server_content.output_transcription.text
                            accumulated_output_transcripts.append(out_txt)
                            logger.debug(f"[GeminiLive Rx #{msg_idx}] output_transcription: \"{out_txt}\"")

                        model_turn = getattr(server_content, "model_turn", None)
                        if model_turn:
                            logger.debug(f"[GeminiLive Rx #{msg_idx}] model_turn with {len(model_turn.parts)} parts")
                            for p_idx, part in enumerate(model_turn.parts):
                                if self._abort_requested.is_set():
                                    break

                                # Text token stream
                                if hasattr(part, "text") and part.text:
                                    token = part.text
                                    accumulated_text.append(token)
                                    logger.debug(f"[GeminiLive Rx #{msg_idx}] part[{p_idx}] text: {len(token)} chars")
                                    if on_text_token:
                                        on_text_token(token)

                                # Audio PCM stream (24kHz 16-bit Mono)
                                if hasattr(part, "inline_data") and part.inline_data:
                                    pcm_bytes = part.inline_data.data
                                    mime = getattr(part.inline_data, "mime_type", "audio/pcm;rate=24000")
                                    if pcm_bytes:
                                        total_audio_bytes += len(pcm_bytes)
                                        audio_chunks_count += 1
                                        logger.debug(f"[GeminiLive Rx #{msg_idx}] part[{p_idx}] audio/pcm: {len(pcm_bytes)} bytes ({mime})")
                                        if not self._is_speaking.is_set():
                                            print("[GeminiLive] Playing answer...")
                                            if on_speaking_start:
                                                try:
                                                    on_speaking_start()
                                                except Exception:
                                                    pass
                                        self._is_speaking.set()

                                        if on_audio_data:
                                            on_audio_data(pcm_bytes)

                                        # Direct low-latency speaker playback
                                        if speaker_stream and not self._abort_requested.is_set():
                                            try:
                                                if not speaker_stream.active:
                                                    speaker_stream.start()
                                                speaker_stream.write(pcm_bytes)
                                            except Exception as write_err:
                                                logger.debug(f"[GeminiLiveClient] Speaker write notice: {write_err}")

                        if getattr(server_content, "turn_complete", False):
                            logger.debug(f"[GeminiLive Rx #{msg_idx}] server_content.turn_complete=True")
                            break
                    else:
                        logger.debug(f"[GeminiLive Rx #{msg_idx}] Non-server_content event: {resp}")

                # Allow buffered speaker audio to finish playing naturally
                if play_audio and speaker_stream and total_audio_bytes > 0 and not self._abort_requested.is_set():
                    try:
                        time.sleep(0.25)
                    except Exception:
                        pass

        finally:
            self._is_active.clear()
            self._is_speaking.clear()
            self._active_session = None

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        # Prioritize clean output audio transcription over raw text tokens (which can contain model thinking)
        full_reply_text = "".join(accumulated_output_transcripts).strip()
        raw_model_tokens = "".join(accumulated_text).strip()
        if not full_reply_text:
            full_reply_text = raw_model_tokens

        # Check for <<TARGET: [ymin, xmin, ymax, xmax]>> in output transcription or raw model tokens
        clean_reply_text, target_box = parse_target_coordinates(full_reply_text, window_metadata)
        if not target_box and raw_model_tokens:
            _, target_box = parse_target_coordinates(raw_model_tokens, window_metadata)

        user_spoken_query = "".join(accumulated_user_transcripts).strip()

        # Update rolling session history
        if clean_reply_text and not self._abort_requested.is_set():
            history_query = user_spoken_query or user_query or "[Voice Query (Native Audio)]"
            self.memory.add_turn(
                user_query=history_query,
                model_reply=clean_reply_text,
                window_title=win_title,
                process_name=win_proc
            )

        return {
            "status": "success" if not self._abort_requested.is_set() else "aborted",
            "reply_text": clean_reply_text,
            "user_query": user_spoken_query or user_query or "",
            "audio_bytes": total_audio_bytes,
            "audio_chunks": audio_chunks_count,
            "duration_ms": elapsed_ms,
            "window_title": win_title,
            "process_name": win_proc,
            "target_box": target_box
        }

    def execute_turn_sync(
        self,
        user_query: Optional[str] = None,
        audio_pcm: Optional[bytes] = None,
        image_bytes: Optional[bytes] = None,
        window_metadata: Optional[Dict[str, Any]] = None,
        play_audio: bool = True,
        on_speaking_start: Optional[Callable[[], None]] = None,
        on_text_token: Optional[Callable[[str], None]] = None
    ) -> Dict[str, Any]:
        """Synchronous wrapper for execute_turn_async."""
        return asyncio.run(self.execute_turn_async(
            user_query=user_query,
            audio_pcm=audio_pcm,
            image_bytes=image_bytes,
            window_metadata=window_metadata,
            play_audio=play_audio,
            on_speaking_start=on_speaking_start,
            on_text_token=on_text_token
        ))


def capture_and_resize_screen(max_width: int = 1280, quality: int = 65) -> Optional[bytes]:
    """
    Adaptive Vision Streamer frame generator:
    - Captures screen with all_screens=True (multi-monitor safe).
    - Resizes to max width 1280px (preserves aspect ratio).
    - Encodes JPEG quality 65% in-memory (0 disk I/O).
    """
    try:
        from visual_cortex import ensure_interactive_station
        ensure_interactive_station()
        from PIL import Image, ImageGrab
        try:
            img = ImageGrab.grab(all_screens=True)
        except Exception:
            try:
                img = ImageGrab.grab()
            except Exception:
                img = Image.new("RGB", (1280, 720), color=(20, 24, 30))

        w, h = img.size
        if w > max_width:
            new_h = int(h * max_width / w)
            img = img.resize((max_width, new_h), Image.Resampling.BILINEAR)
        if img.mode != "RGB":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"[VisionStreamer] Screen grab/resize error: {e}")
        return None


class FullDuplexLiveSession:
    """
    Pure Full-Duplex Gemini Live Session:
    - Runs continuous bidirectional streaming via Gemini Live WebSocket (bidiGenerateContent).
    - Continuous Microphone Streaming: raw PCM 16kHz sent via send_realtime_input().
    - Adaptive Vision Streamer: 1 FPS screen capture (max 1280px, quality 65% JPEG).
    - Native Barge-in: START_OF_ACTIVITY_INTERRUPTS halts speaker output instantly (<50ms) on user speech.
    - Low-latency Audio Output: streams 24kHz audio chunks to speakers.
    - Non-blocking background event loop in dedicated daemon thread.
    - Clean teardown: closes WebSocket, cancels tasks, stops mic stream, restores system audio volume.
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-3.1-flash-live-preview",
        voice_name: str = "Aoede",
        on_speaking_start: Optional[Callable[[], None]] = None,
        on_barge_in: Optional[Callable[[], None]] = None,
        on_turn_complete: Optional[Callable[[], None]] = None
    ):
        self.api_key = api_key
        self.model_name = model_name
        self.voice_name = voice_name
        self.on_speaking_start = on_speaking_start
        self.on_barge_in = on_barge_in
        self.on_turn_complete = on_turn_complete

        self._stop_event = threading.Event()
        self._is_active = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._speaker_stream: Optional[sd.RawOutputStream] = None
        self._speaker_queue = queue.Queue()
        self._speaker_thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._audio_queue: Optional[asyncio.Queue] = None

    @property
    def is_active(self) -> bool:
        return self._is_active.is_set() and not self._stop_event.is_set()

    def start(self):
        """Starts the full-duplex session in a background daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_thread,
            name="FullDuplexLiveSessionThread",
            daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0):
        """Gracefully closes the session, cancels image/mic tasks, and releases audio resources."""
        self._stop_event.set()
        self._is_active.clear()

        # Stop speaker output immediately
        self._instant_halt_playback()

        # Unsubscribe mic stream
        try:
            from audio_recorder import UnifiedAudioStream
            UnifiedAudioStream.get_instance().stop()
        except Exception:
            pass

        # Unduck background audio
        try:
            from audio_ducker import audio_ducker
            if audio_ducker:
                audio_ducker.unduck()
        except Exception:
            pass

        if self._speaker_thread and self._speaker_thread.is_alive():
            self._speaker_thread.join(timeout=1.0)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _ensure_speaker_stream(self) -> sd.RawOutputStream:
        if self._speaker_stream is None or not getattr(self._speaker_stream, "active", False):
            try:
                if self._speaker_stream is not None:
                    try:
                        self._speaker_stream.close()
                    except Exception:
                        pass
                self._speaker_stream = sd.RawOutputStream(
                    samplerate=24000,
                    channels=1,
                    dtype="int16",
                    blocksize=2048
                )
                self._speaker_stream.start()
            except Exception as e:
                print(f"[LiveClient:Speaker Error] Failed to open 24kHz speaker stream: {e}", flush=True)
                traceback.print_exc()
                raise
        return self._speaker_stream

    def _ensure_speaker_worker(self):
        if self._speaker_thread is None or not self._speaker_thread.is_alive():
            self._speaker_thread = threading.Thread(
                target=self._speaker_playback_loop,
                name="LiveSpeakerPlaybackWorker",
                daemon=True
            )
            self._speaker_thread.start()

    def _speaker_playback_loop(self):
        """Dedicated audio playback loop consuming 24kHz PCM chunks without blocking the async event loop."""
        while not self._stop_event.is_set():
            try:
                pcm_bytes = self._speaker_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if self._stop_event.is_set():
                break
            try:
                stream = self._ensure_speaker_stream()
                if not stream.active:
                    stream.start()
                stream.write(pcm_bytes)
            except Exception as e:
                print(f"[LiveClient:Speaker Error] Output playback exception: {e}", flush=True)
                traceback.print_exc()
            finally:
                self._speaker_queue.task_done()

    def _instant_halt_playback(self):
        """Instantly halts speaker playback (<50ms) and flushes output buffer for native barge-in."""
        # 1. Drain speaker queue immediately
        while not self._speaker_queue.empty():
            try:
                self._speaker_queue.get_nowait()
                self._speaker_queue.task_done()
            except Exception:
                break

        # 2. Stop sounddevice output (< 50ms)
        try:
            sd.stop()
        except Exception:
            pass

        # 3. Abort active speaker stream
        if self._speaker_stream is not None:
            try:
                if hasattr(self._speaker_stream, "abort"):
                    self._speaker_stream.abort()
                elif hasattr(self._speaker_stream, "stop"):
                    self._speaker_stream.stop()
                self._speaker_stream.close()
            except Exception:
                pass
            self._speaker_stream = None

    def _run_thread(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_session_loop())
        except Exception as e:
            print(f"[LiveClient:Session Error] Exception in session loop: {e}", flush=True)
            traceback.print_exc()
        finally:
            self._is_active.clear()
            try:
                self._instant_halt_playback()
            except Exception:
                pass
            try:
                pending = asyncio.all_tasks(self._loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            self._loop.close()
            self._loop = None

    async def _async_session_loop(self):
        client = genai.Client(
            api_key=self.api_key,
            http_options=types.HttpOptions(api_version="v1alpha")
        )

        live_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice_name)
                )
            ),
            thinking_config=types.ThinkingConfig(
                thinking_budget=0
            ),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                activity_handling=types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS
            ),
            system_instruction=types.Content(
                parts=[types.Part.from_text(
                    text="คุณคือ Gemini Live Multimodal AI Voice Copilot ประจำเครื่องคอมพิวเตอร์ ทำหน้าที่เป็นผู้ช่วยอัจฉริยะ ตอบกลับเป็นภาษาไทยด้วยเสียงที่กระชับ ตรงจุด และรวดเร็ว "
                         "คุณมองเห็นหน้าจอของผู้ใช้ผ่านการส่งภาพอัตโนมัติ 1 FPS จงตอบสนองต่อคำพูดและคำถามของผู้ใช้ทันทีอย่างเป็นธรรมชาติ"
                )]
            )
        )

        logger.info(f"[FullDuplexSession] Connecting to Gemini Live ({self.model_name})...")
        print(f"\n[GeminiLive] 🌐 Connecting Full-Duplex session to {self.model_name}...", flush=True)

        from audio_recorder import UnifiedAudioStream
        from audio_ducker import audio_ducker

        self._audio_queue = asyncio.Queue()
        self._ensure_speaker_worker()
        loop = self._loop

        def _mic_audio_subscriber(chunk_np, raw_pcm):
            if not self._stop_event.is_set() and self._audio_queue is not None:
                try:
                    loop.call_soon_threadsafe(self._audio_queue.put_nowait, raw_pcm)
                except Exception:
                    pass

        # Duck Windows background audio immediately
        if audio_ducker:
            try:
                audio_ducker.duck(0.0)
            except Exception:
                pass

        try:
            print("[LiveClient] Connecting to Gemini Live WebSocket...", flush=True)
            async with client.aio.live.connect(model=self.model_name, config=live_config) as session:
                self._is_active.set()
                print("[LiveClient] WebSocket handshake successful", flush=True)
                print("[GeminiLive] 🟢 Session Connected! Full-Duplex Live (1 FPS Vision + Mic Streaming Active).", flush=True)

                # Start mic stream
                stream = UnifiedAudioStream.get_instance()
                stream.subscribe(_mic_audio_subscriber)
                stream.set_muted(False)
                stream.start(asynchronous=True)

                audio_chunk_count = 0

                # Concurrent Task 1: Continuous Mic Streaming
                async def _mic_worker():
                    nonlocal audio_chunk_count
                    try:
                        while not self._stop_event.is_set():
                            try:
                                chunk = await asyncio.wait_for(self._audio_queue.get(), timeout=0.25)
                            except asyncio.TimeoutError:
                                continue
                            if self._stop_event.is_set():
                                break
                            await session.send_realtime_input(
                                media=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                            )
                            audio_chunk_count += 1
                            if audio_chunk_count % 50 == 0:
                                print(f"[LiveClient] Audio chunk sent (throttle logging to once every 50 chunks: chunk #{audio_chunk_count}, {len(chunk)} bytes)", flush=True)
                    except Exception as e:
                        print(f"[LiveClient:Mic Error] Send exception: {e}", flush=True)
                        traceback.print_exc()

                # Concurrent Task 2: Adaptive 1 FPS Vision Streamer
                async def _vision_worker():
                    try:
                        await asyncio.sleep(0.1)
                        while not self._stop_event.is_set():
                            t0 = time.perf_counter()
                            try:
                                jpeg_bytes = await loop.run_in_executor(None, capture_and_resize_screen, 1280, 65)
                                if jpeg_bytes and not self._stop_event.is_set():
                                    await session.send_realtime_input(
                                        media=types.Blob(data=jpeg_bytes, mime_type="image/jpeg")
                                    )
                                    print(f"[LiveClient] Vision frame sent (size: {len(jpeg_bytes)} bytes)", flush=True)
                            except Exception as e:
                                print(f"[LiveClient:Vision Error] Frame send exception: {e}", flush=True)
                                traceback.print_exc()

                            elapsed = time.perf_counter() - t0
                            sleep_time = max(0.05, 1.0 - elapsed)
                            for _ in range(int(sleep_time / 0.1)):
                                if self._stop_event.is_set():
                                    return
                                await asyncio.sleep(0.1)
                    except Exception as e:
                        print(f"[LiveClient:Vision Worker Error] {e}", flush=True)
                        traceback.print_exc()

                # Concurrent Task 3: Inbound Server Stream (Audio Playback + Native Barge-in)
                async def _receive_worker():
                    try:
                        async for resp in session.receive():
                            if self._stop_event.is_set():
                                break

                            server_content = getattr(resp, "server_content", None)
                            if server_content is None:
                                continue

                            # Native Barge-in detection
                            if getattr(server_content, "interrupted", False):
                                print("[LiveClient] Received server chunk: [Interruption]", flush=True)
                                print("\n[GeminiLive] ⚡ Native Barge-in: User interrupted, halting playback instantly.", flush=True)
                                self._instant_halt_playback()
                                if self.on_barge_in:
                                    try:
                                        self.on_barge_in()
                                    except Exception:
                                        pass
                                continue

                            # Text transcriptions
                            user_txt = ""
                            if getattr(server_content, "input_transcription", None) and server_content.input_transcription.text:
                                user_txt = server_content.input_transcription.text.strip()
                                if user_txt:
                                    print(f"[User Voice]: \"{user_txt}\"", flush=True)

                            ai_txt = ""
                            if getattr(server_content, "output_transcription", None) and server_content.output_transcription.text:
                                ai_txt = server_content.output_transcription.text.strip()
                                if ai_txt:
                                    print(f"[Gemini]: \"{ai_txt}\"", flush=True)

                            # Model audio streaming
                            model_turn = getattr(server_content, "model_turn", None)
                            if model_turn:
                                for part in model_turn.parts:
                                    if self._stop_event.is_set():
                                        break
                                    part_text = getattr(part, "text", "") or ""
                                    if hasattr(part, "inline_data") and part.inline_data:
                                        pcm_bytes = part.inline_data.data
                                        if pcm_bytes and not self._stop_event.is_set():
                                            text_desc = f" | Text: '{part_text[:30]}...'" if part_text else (f" | Text: '{ai_txt[:30]}...'" if ai_txt else "")
                                            print(f"[LiveClient] Received server chunk: [Audio: {len(pcm_bytes)} bytes{text_desc}]", flush=True)
                                            # Route directly to dedicated non-blocking playback worker
                                            self._speaker_queue.put(pcm_bytes)
                                    elif part_text:
                                        print(f"[LiveClient] Received server chunk: [Text: '{part_text[:40]}']", flush=True)

                            if getattr(server_content, "turn_complete", False):
                                if self.on_turn_complete:
                                    try:
                                        self.on_turn_complete()
                                    except Exception:
                                        pass
                    except Exception as rx_e:
                        print(f"[LiveClient:Receive Error] Exception in receive stream: {rx_e}", flush=True)
                        traceback.print_exc()

                tasks = [
                    asyncio.create_task(_mic_worker(), name="MicWorker"),
                    asyncio.create_task(_vision_worker(), name="VisionWorker"),
                    asyncio.create_task(_receive_worker(), name="ReceiveWorker")
                ]

                while not self._stop_event.is_set():
                    if any(t.done() for t in tasks):
                        for t in tasks:
                            if t.done() and not t.cancelled():
                                exc = t.exception()
                                if exc:
                                    print(f"[LiveClient] Task {t.get_name()} terminated with error: {exc}", flush=True)
                        break
                    await asyncio.sleep(0.1)

                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

                stream.unsubscribe(_mic_audio_subscriber)
                stream.stop()
        except Exception as conn_e:
            print(f"[LiveClient:Connection Error] WebSocket live session failure: {conn_e}", flush=True)
            traceback.print_exc()
        finally:
            if audio_ducker:
                try:
                    audio_ducker.unduck()
                except Exception:
                    pass
            print("[GeminiLive] 🔴 Session Disconnected. Microphone closed, audio unducked.", flush=True)


class GeminiLiveCopilotController:
    """
    Integrated controller linking:
    1. Dual Wake Trigger (F20 or wake phrase "เจมิไนมาช่วยหน่อย")
    2. Zero-disk window capture (visual_cortex)
    3. GeminiLiveClient with context fusion
    4. FSM lifecycle management & F20 kill-switch
    """

    WAKE_PHRASES = [
        "เจมิไนมาช่วยหน่อย",
        "เจมิไนช่วยหน่อย",
        "gemini help",
        "gemini come help"
    ]

    def __init__(self, api_key: Optional[str] = None):
        self.client = GeminiLiveClient(api_key=api_key)
        self.fsm = LiveCopilotFSM(
            on_state_change=self._on_fsm_state_change
        )
        self._active_worker: Optional[threading.Thread] = None

    def _on_fsm_state_change(self, old: CopilotState, new: CopilotState):
        logger.info(f"[Controller FSM] State changed: {old.value} -> {new.value}")

    def is_wake_phrase(self, text: str) -> bool:
        """Checks if transcribed text contains the Thai or English dual-wake phrase."""
        cleaned = text.strip().lower()
        return any(phrase in cleaned for phrase in self.WAKE_PHRASES)

    def trigger_copilot(self, user_query: str = "หน้าต่างนี้คืออะไร ช่วยสรุปให้หน่อย"):
        """
        Triggers the full Gemini Live Multimodal Pipeline:
        Captures window at cursor -> Fuses context -> Streams voice response.
        """
        if self.fsm.state == CopilotState.ACTIVE:
            logger.info("[Controller] F20 Kill-Switch: Already ACTIVE -> Stopping...")
            self.client.abort()
            self.fsm.toggle_f20()  # Rollback to STANDBY
            return

        # STANDBY -> ACTIVE
        self.fsm.toggle_f20()

        def _worker():
            try:
                # Capture current window context
                ctx = self.fsm.current_context or capture_window_at_cursor()
                img_bytes = ctx.get("image_bytes")

                print(f"\n[Gemini Live Copilot] 🎙️ Multimodal Turn Active:")
                print(f"  Target Window: \"{ctx.get('title')}\" ({ctx.get('process_name')})")
                print(f"  Voice Query:   \"{user_query}\"")
                print(f"  Streaming response to speakers (Press F20 to kill)...")

                res = self.client.execute_turn_sync(
                    user_query=user_query,
                    image_bytes=img_bytes,
                    window_metadata=ctx,
                    play_audio=True
                )
                print(f"[Gemini Live Copilot] Finished in {res['duration_ms']:.1f}ms ({res['audio_bytes']:,} audio bytes received).")
            except Exception as e:
                logger.error(f"[Gemini Live Copilot Error]: {e}")
            finally:
                if self.fsm.is_active:
                    self.fsm.toggle_f20()  # Revert to STANDBY on turn complete

        self._active_worker = threading.Thread(target=_worker, name="GeminiLiveWorker", daemon=True)
        self._active_worker.start()


def run_gemini_live_verification() -> bool:
    """
    Automated Self-Verification Test:
    Fuses previous context + window image + Thai query, verifies Gemini Live returns audio bytes (Exit 0).
    """
    print("=" * 65)
    print(" GEMINI LIVE MULTIMODAL PIPELINE: END-TO-END VERIFICATION")
    print("=" * 65)

    # 1. Initialize Client
    print("\n[Step 1] Initializing Gemini Live Client...")
    client = GeminiLiveClient()
    if not client.api_key:
        print("         -> FAILED: GEMINI_API_KEY not found in environment or .env")
        return False
    print(f"         API Key Loaded: {client.api_key[:8]}... (Model: {client.model_name})")
    print("         -> PASSED")

    # 2. Seed Rolling Session Memory (Previous Context)
    print("\n[Step 2] Seeding Previous Conversation Context...")
    client.memory.add_turn(
        user_query="เปิดโปรเจกต์ voice-in ใน VS Code ให้หน่อย",
        model_reply="เปิดโปรเจกต์ voice-in เรียบร้อยแล้วครับ",
        window_title="07. voice-in - Antigravity IDE",
        process_name="Antigravity IDE.exe"
    )
    context_preview = client.memory.format_history_for_fusion()
    print(f"         Previous Context Injected:\n{context_preview}")
    print("         -> PASSED")

    # 3. Harvest Target Window Context & Image (Visual Cortex)
    print("\n[Step 3] Perceiving Target Window Context via Visual Cortex...")
    target_context = look_at_cursor()
    print(f"         Window Title:  \"{target_context.get('title')}\"")
    print(f"         Process Name:  {target_context.get('process_name')}")
    print(f"         Dimensions:    {target_context.get('dimensions', {}).get('width')}x{target_context.get('dimensions', {}).get('height')} px")
    print(f"         Image Buffer:  {len(target_context.get('image_bytes', b'')):,} bytes")
    assert len(target_context.get("image_bytes", b"")) > 0, "Screenshot image_bytes cannot be empty"
    print("         -> PASSED (Zero-disk screen capture acquired)")

    # 4. Dispatch Multimodal Query & Stream Audio
    test_query = "หน้านี้คือโปรแกรมอะไร ตอบสั้นๆ คำเดียว"
    print(f"\n[Step 4] Dispatching Multimodal Query with Context Fusion...")
    print(f"         Query: \"{test_query}\"")
    print("         Connecting to Gemini Live WebSocket & receiving audio...")

    result = client.execute_turn_sync(
        user_query=test_query,
        image_bytes=target_context.get("image_bytes"),
        window_metadata=target_context,
        play_audio=True
    )

    print(f"\n[Step 5] Validating Response Metrics:")
    print(f"         - Status:        {result['status']}")
    print(f"         - Model Reply:   \"{result['reply_text']}\"")
    print(f"         - Audio Bytes:   {result['audio_bytes']:,} bytes (24kHz Mono)")
    print(f"         - Audio Chunks:  {result['audio_chunks']} chunks")
    print(f"         - Round-Trip:    {result['duration_ms']:.1f}ms")

    assert result["status"] == "success", f"Expected success, got {result['status']}"
    assert result["audio_bytes"] > 0, "Expected non-zero audio bytes from Gemini Live"
    assert result["audio_chunks"] > 0, "Expected at least 1 audio chunk"
    print("         -> PASSED (Audio output validated)")

    # 5b. Verify Native Audio Streaming (Simulating 4.38s captured PTT audio buffer)
    print("\n[Step 5b] Dispatching Native Audio Streaming (Simulating 4.38s / 140KB Audio Buffer)...")
    import numpy as np
    target_samples = int(16000 * 4.38)  # 4.38s at 16kHz Mono 16-bit = 70,080 samples = 140,160 bytes (~136.8 KB)
    if _GREETING_PCM:
        # Resample greeting.wav (24k) to 16k using linear interpolation
        pcm24 = np.frombuffer(_GREETING_PCM, dtype=np.int16)
        num_16k = int(len(pcm24) * 16000 / 24000)
        pcm16 = np.interp(np.linspace(0, len(pcm24) - 1, num_16k), np.arange(len(pcm24)), pcm24).astype(np.int16)
        # Pad / loop to 4.38s
        repeats = (target_samples // len(pcm16)) + 1
        simulated_pcm = np.tile(pcm16, repeats)[:target_samples].tobytes()
    else:
        t = np.linspace(0, 4.38, target_samples, False)
        simulated_pcm = (np.sin(2 * np.pi * 440 * t) * 3000).astype(np.int16).tobytes()

    print(f"         Buffer Size:     {len(simulated_pcm):,} bytes (4.38s @ 16kHz 16-bit Mono)")
    res_audio = client.execute_turn_sync(
        user_query=None,
        audio_pcm=simulated_pcm,
        play_audio=True
    )
    print(f"         - Status:        {res_audio['status']}")
    print(f"         - Model Reply:   \"{res_audio['reply_text']}\"")
    print(f"         - User Spoken:   \"{res_audio.get('user_query', '')}\"")
    print(f"         - Audio Bytes:   {res_audio['audio_bytes']:,} bytes (Native Audio Response)")
    print(f"         - Audio Chunks:  {res_audio['audio_chunks']} chunks")
    print(f"         - Round-Trip:    {res_audio['duration_ms']:.1f}ms")
    assert res_audio["status"] == "success", f"Expected success, got {res_audio['status']}"
    assert res_audio["audio_bytes"] > 0, "Expected non-empty audio frames from Gemini Live"
    assert res_audio["audio_chunks"] > 0, "Expected at least 1 audio chunk"
    assert len(res_audio["reply_text"]) > 0, "Expected non-empty reply text"
    print("         -> PASSED (Native audio streaming & 4.38s buffer verified)")

    # 6. Verify Session Memory Updated with New Turn
    print("\n[Step 6] Verifying Memory Continuity for Next Session...")
    assert len(client.memory.turns) >= 2, "Expected at least 2 turns in memory"
    print(f"         Updated History: {len(client.memory.turns)} turns saved cleanly")
    print("         -> PASSED")

    print("\n" + "=" * 65)
    print(" ALL GEMINI LIVE MULTIMODAL PIPELINE CHECKS PASSED (EXIT 0)")
    print("=" * 65)
    return True


def main():
    parser = argparse.ArgumentParser(description="Windows Edge - Gemini Live Multimodal Pipeline")
    parser.add_argument("--test", action="store_true", help="Run automated end-to-end multimodal verification test")
    parser.add_argument("--query", type=str, default="หน้าต่างนี้คืออะไร ตอบเป็นภาษาไทยสั้นๆ", help="User voice/text query to test")
    args = parser.parse_args()

    if args.test:
        success = run_gemini_live_verification()
        sys.exit(0 if success else 1)
    else:
        controller = GeminiLiveCopilotController()
        controller.trigger_copilot(user_query=args.query)
        # Keep process alive for worker
        time.sleep(5.0)


if __name__ == "__main__":
    main()
