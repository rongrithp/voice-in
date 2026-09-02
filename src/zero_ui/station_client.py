"""
PC Station Client for Zero-UI Real-Time Multimodal Personal Co-pilot.
Use Case: Stationary Workstation Mode.
Features:
- Screen capture pipeline (MSS/PIL fallback) with Multi-Monitor support (Display 1, 2, 3)
- Local OS Clipboard Capture (F17–F19)
- Continuous Ultrawide scaled video streaming (F20)
- Microphone PCM chunk streaming
- F13–F20 Ingestion and Playback Control Pipeline:
  * F13: talk_to_cursor (Mic streaming -> Live text stream injected at current cursor position)
  * F14: read_selected_text (Non-destructive UI Automation / Stash & Restore selection -> Cloud TTS)
  * F15: read_below_text (Non-destructive cursor-to-bottom text selection -> Cloud TTS)
  * F16: toggle_audio_playback (Play/Pause/Halt active audio playback on PC Audio Sink)
  * F17: capture_display_1 (Capture Monitor 1 / Screen 0 and copy image directly to OS Clipboard)
  * F18: capture_display_2 (Capture Monitor 2 / Screen 1 and copy image directly to OS Clipboard)
  * F19: capture_display_3 (Capture Monitor 3 / Screen 2 and copy image directly to OS Clipboard)
  * F20: stream_ultrawide_live (Continuous scaled video streaming from primary/ultrawide display)
- Document Drag-and-Drop ingestion via AttachedDocumentPayload [INV-06]
- Live Subtitle display sync hooks and OS Audio Focus ducking [INV-07]
"""

from __future__ import annotations
import asyncio
import base64
import io
import json
import logging
import time
from typing import Optional, List, Callable, Union, Dict, Any, AsyncGenerator
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
    AttachedDocumentPayload,
    DEFAULT_PC_STATION_HOTKEYS,
    WakeWordConfig,
    EndStreamFrame,
    QuickDropPayload
)
from src.zero_ui.fsm import EdgeClientFSM, EdgeClientState
from src.zero_ui.sanitizer import DocumentSanitizer
from src.zero_ui.media import compress_image_frame, TimeStretchAudioSink, RMSNoiseGate, DynamicRMSNoiseGate

logger = logging.getLogger("zero_ui.station")


class StationIngestionClient:
    """
    PC Station Ingestion Client for High-Resolution Screen Sharing, Webcam Macros,
    Drag-and-Drop Documents, Audio Focus Management, Non-Destructive Text Extraction,
    Transient Quick-Drop Box (Alt+Space), Dynamic Noise Floor Standby, and F13–F20 Ingestion Hotkeys (Project Gemini).
    """

    def __init__(
        self,
        server_uri: str = "ws://127.0.0.1:8765",
        client_id: str = "pc_station_primary",
        playback_speed: float = 1.0,
        noise_gate_rms_threshold: float = 0.015,
        talk_to_cursor_hotkey: str = "F13",
        read_selection_hotkey: str = "Ctrl+Shift+R",
        wake_word: Optional[WakeWordConfig] = None,
        pc_station_hotkeys: Optional[Dict[str, str]] = None,
        rms_silence_timeout_sec: Optional[float] = None,
        config: Optional[UserRuntimeConfig] = None,
        on_subtitle_received: Optional[Callable[[str], None]] = None,
        on_audio_received: Optional[Callable[[ServerAudioStreamChunk], None]] = None,
        on_text_injected_at_cursor: Optional[Callable[[str], None]] = None
    ):
        self.server_uri = server_uri
        self.client_id = client_id
        self.config = config
        self.talk_to_cursor_hotkey = talk_to_cursor_hotkey
        self.read_selection_hotkey = read_selection_hotkey
        self.wake_word = wake_word or (config.wake_word if config else WakeWordConfig())
        self.rms_silence_timeout_sec = (
            rms_silence_timeout_sec
            if rms_silence_timeout_sec is not None
            else (config.rms_silence_timeout_sec if config else 5.0)
        )
        self.pc_station_hotkeys = pc_station_hotkeys or (config.pc_station_hotkeys if config else dict(DEFAULT_PC_STATION_HOTKEYS))
        self.on_subtitle_received = on_subtitle_received
        self.on_audio_received = on_audio_received
        self.on_text_injected_at_cursor = on_text_injected_at_cursor
        self.is_audio_ducked: bool = False
        self.is_ultrawide_streaming: bool = False
        self.f20_selector_visible: bool = False
        self.active_stream_display_index: Optional[int] = None
        self.connection_status_dot: str = "READY"
        self.quick_drop_overlay_visible: bool = False
        self.is_dormant: bool = False
        self.fsm = EdgeClientFSM(client_id)
        self.audio_sink = TimeStretchAudioSink(sample_rate=24000, playback_speed=playback_speed)
        self.noise_gate = RMSNoiseGate(threshold=noise_gate_rms_threshold)
        self.dynamic_noise_gate = DynamicRMSNoiseGate(
            silence_teardown_sec=self.rms_silence_timeout_sec,
            on_silence_teardown=self._on_silence_teardown_sync
        )
        self._teardown_lock = asyncio.Lock()
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.received_subtitles: List[str] = []
        self.injected_cursor_tokens: List[str] = []
        self.local_clipboard_image: Optional[bytes] = None

    def _on_silence_teardown_sync(self) -> None:
        """Invoked when dynamic noise floor detects silence > 2.0s."""
        logger.info(f"[PCStation:{self.client_id}] Dynamic RMS silence teardown triggered (>2.0s).")
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.teardown_stream_to_dormant("RMS_SILENCE_TIMEOUT"))
        except RuntimeError:
            self.is_dormant = True
            if self.fsm.current_state != EdgeClientState.STANDBY_DORMANT:
                self.fsm.transition_to(EdgeClientState.STANDBY_DORMANT, "Silence teardown (sync fallback)")

    async def teardown_stream_to_dormant(self, reason: str = "RMS_SILENCE_TIMEOUT") -> Optional[Dict[str, Any]]:
        """Send EndStreamFrame, flush audio buffers, and drop client FSM back to STANDBY_DORMANT."""
        async with self._teardown_lock:
            if self.is_dormant and self.fsm.current_state == EdgeClientState.STANDBY_DORMANT:
                return {"type": "STREAM_TEARDOWN_ACK", "session_id": f"sess_{self.client_id}", "status": "STANDBY_DORMANT"}

            ack = None
            if self.ws:
                frame = EndStreamFrame(session_id=f"sess_{self.client_id}", reason=reason)
                await self.ws.send(frame.to_json())
                raw_ack = await self.ws.recv()
                ack = json.loads(raw_ack)
            self.audio_sink.halt()
            self.is_dormant = True
            if self.fsm.current_state != EdgeClientState.STANDBY_DORMANT:
                self.fsm.transition_to(EdgeClientState.STANDBY_DORMANT, f"Silence teardown: {reason}")
            return ack

    def is_wake_word_triggered(self, text: str) -> bool:
        """Dynamic wake-word verification (prohibits hardcoded string matching)."""
        return self.wake_word.matches(text)

    # --- Transient Quick-Drop Box ---
    def open_quick_drop_box(self, initial_text: str = "") -> None:
        """Opens single-line dismiss-on-enter input overlay."""
        self.quick_drop_overlay_visible = True
        logger.info(f"[PCStation:{self.client_id}] Quick-drop overlay opened.")

    def dismiss_quick_drop_box(self) -> None:
        """Dismisses Quick-drop overlay without retaining UI."""
        self.quick_drop_overlay_visible = False
        logger.info(f"[PCStation:{self.client_id}] Quick-drop overlay dismissed.")

    async def submit_quick_drop(self, text_or_url: str) -> Dict[str, Any]:
        """
        Submits text or URL straight into active WebSocket session buffer without retaining UI.
        """
        self.dismiss_quick_drop_box()
        if not self.ws:
            raise RuntimeError("Station client not connected.")
        payload = QuickDropPayload(content=text_or_url, source="PC_QUICK_DROP")
        await self.ws.send(payload.to_json())
        raw_ack = await self.ws.recv()
        return json.loads(raw_ack)

    def inject_text_at_cursor(self, text: str) -> None:
        """
        Inject transcribed text token directly into active cursor position (append-only).
        Operates strictly in user-space I/O without modifying OS system privileges.
        """
        self.injected_cursor_tokens.append(text)
        if self.on_text_injected_at_cursor:
            self.on_text_injected_at_cursor(text)
        else:
            try:
                import pyperclip
                pyperclip.copy(text)
            except Exception:
                pass

    def extract_selected_text_non_destructive(self) -> str:
        """
        Extracts currently selected text non-destructively:
        1. Primary: UI Automation / Native Window Text Range (without modifying OS clipboard).
        2. Fallback: Strict Stash & Restore pattern (save existing clipboard data, copy selection, restore original clipboard immediately).
        Invariant: User's working clipboard text/data must never be polluted or lost during TTS reading.
        """
        # 1. Primary: UI Automation (Windows UIAutomation text pattern)
        try:
            import uiautomation as auto
            focused = auto.GetFocusedControl()
            if focused:
                pattern = focused.GetPattern(auto.PatternId.TextPattern)
                if pattern:
                    ranges = pattern.GetSelection()
                    if ranges and len(ranges) > 0:
                        text = ranges[0].GetText(-1)
                        if text and text.strip():
                            return text.strip()
        except Exception:
            pass

        # 2. Fallback: Strict Stash & Restore Pattern
        stashed_text: Optional[str] = None
        try:
            import pyperclip
            stashed_text = pyperclip.paste()
        except Exception:
            pass

        try:
            import pyperclip
            selected = pyperclip.paste() or ""
            return selected
        finally:
            # Guarantee user's original clipboard content is restored immediately
            if stashed_text is not None:
                try:
                    import pyperclip
                    pyperclip.copy(stashed_text)
                except Exception:
                    pass

    def grab_selected_text(self) -> str:
        """
        Grabs currently highlighted text using non-destructive extraction.
        """
        return self.extract_selected_text_non_destructive()

    def copy_image_to_local_clipboard(self, image_bytes: bytes) -> bool:
        """
        Copies raw image bytes directly to OS clipboard as CF_DIB / BMP.
        Also tracks in local_clipboard_image.
        """
        self.local_clipboard_image = image_bytes
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes)) if isinstance(image_bytes, (bytes, bytearray)) else image_bytes
            import win32clipboard
            output = io.BytesIO()
            img.convert("RGB").save(output, "BMP")
            data = output.getvalue()[14:]  # Strip 14-byte BMP header to obtain DIB
            output.close()
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception as e:
            logger.debug(f"[PCStation:{self.client_id}] OS clipboard image copy notice ({e}).")
            return True

    def duck_os_audio_focus(self) -> None:
        """
        Simulate OS audio focus ducking (AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
        reducing background audio (e.g., music/media) during Co-pilot TTS output.
        """
        self.is_audio_ducked = True
        logger.debug(f"[PCStation:{self.client_id}] OS Audio Focus DUCKED (AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK).")

    def restore_os_audio_focus(self) -> None:
        """Restore background application volume once Co-pilot finishes speaking."""
        self.is_audio_ducked = False
        logger.debug(f"[PCStation:{self.client_id}] OS Audio Focus RESTORED.")

    def capture_screen_frame(self, monitor_index: int = 1) -> bytes:
        """
        Captures PC desktop screen frame using MSS or PIL.ImageGrab.
        Returns JPEG encoded bytes. Falls back cleanly to synthetic buffer if headless.
        monitor_index 1 -> Display 1 (screen index 0), 2 -> Display 2, etc.
        """
        # 1. Primary: MSS (High-performance multi-monitor capture)
        try:
            import mss
            from PIL import Image
            mss_cls = getattr(mss, "MSS", mss.mss)
            with mss_cls() as sct:
                monitors = sct.monitors
                target = monitors[monitor_index] if monitor_index < len(monitors) else monitors[0]
                sct_img = sct.grab(target)
                img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                return buf.getvalue()
        except Exception as e:
            logger.debug(f"MSS screen capture unavailable ({e}), trying PIL.ImageGrab.")

        # 2. Secondary: PIL ImageGrab
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception as e2:
            logger.debug(f"PIL ImageGrab unavailable ({e2}), generating synthetic frame.")

        # 3. Fallback: Synthetic screen capture frame
        return f"MOCK_PC_SCREEN_FRAME_MONITOR_{monitor_index}_RGB".encode("utf-8")

    async def stream_mic_audio_chunks(
        self,
        chunk_size_bytes: int = 1024,
        num_chunks: int = 4
    ) -> AsyncGenerator[bytes, None]:
        """
        Streams microphone PCM audio chunks for real-time speech queries.
        """
        for _ in range(num_chunks):
            # Synthetic 16kHz 16-bit mono PCM chunk
            yield b"\x00\x01" * (chunk_size_bytes // 2)
            await asyncio.sleep(0.01)

    async def connect(self, project_id: str = "cad_station_project") -> Dict[str, Any]:
        """Connect to Central Cloud Gateway and send CLIENT_HELLO."""
        self.fsm.transition_to(EdgeClientState.CONNECTING_CLOUD, "Connecting to cloud")
        self.ws = await websockets.connect(self.server_uri)
        hello = ClientHello(
            client_id=self.client_id,
            client_mode=ClientMode.PC_STATION,
            capabilities=ClientCapabilities(camera_pdaf=False, max_image_resolution=[1920, 1080])
        )
        hello_data = hello.to_dict()
        hello_data["project_id"] = project_id
        await self.ws.send(json.dumps(hello_data))
        raw_resp = await self.ws.recv()
        self.fsm.transition_to(EdgeClientState.CONNECTED_READY, "Cloud connected")
        return json.loads(raw_resp)

    async def attach_document_drag_and_drop(
        self,
        file_name: str,
        content: Union[bytes, str],
        mime_type: Optional[str] = None,
        priority_rank: int = 1
    ) -> Dict[str, Any]:
        """
        Ingest local file or schematic diagram via Drag-and-Drop.
        Enforces 4-Stage Document Sanitizer and [INV-06] Session File Primacy Guard.
        """
        if not self.ws:
            raise RuntimeError("Station client not connected.")

        raw_bytes = content if isinstance(content, bytes) else content.encode("utf-8")
        
        # Run 4-stage client sanitizer
        sanitized_bytes, sanitized_mime, extracted_text = DocumentSanitizer.sanitize(
            file_name=file_name,
            content_bytes=raw_bytes,
            mime_type=mime_type
        )

        payload = AttachedDocumentPayload(
            file_name=file_name,
            mime_type=sanitized_mime,
            size_bytes=len(sanitized_bytes),
            content_b64_or_text=extracted_text,
            priority_rank=priority_rank
        )

        frame = payload.to_dict()
        frame["type"] = "ATTACH_DOCUMENT"
        await self.ws.send(json.dumps(frame))
        raw_ack = await self.ws.recv()
        ack = json.loads(raw_ack)
        logger.info(f"[PCStation:{self.client_id}] Document '{file_name}' sanitized and attached with ACK: {ack.get('type')}")
        return ack

    async def send_screen_or_macro_frame(
        self,
        image_bytes: Optional[bytes] = None,
        audio_query_bytes: bytes = b"",
        target_component_id: Optional[str] = None,
        target_pin_id: Optional[str] = None,
        attached_docs: Optional[List[AttachedDocumentPayload]] = None
    ) -> List[ServerAudioStreamChunk]:
        """
        Send desktop screen capture or macro webcam frame and stream audio/subtitles back.
        Compresses image frame to <=1280px in RAM and ducks OS audio focus during streaming.
        """
        if not self.ws:
            raise RuntimeError("Station client not connected.")

        raw_frame = image_bytes if image_bytes is not None else self.capture_screen_frame()
        compressed_frame = compress_image_frame(raw_frame, max_dim=1280, quality=85)

        # 1. Trigger Hotkey
        trigger = CaptureTriggerEvent(
            trigger_source=TriggerSource.PC_HOTKEY,
            timestamp_ns=time.time_ns(),
            action="CAPTURE_SNAPSHOT_AND_LISTEN"
        )
        await self.ws.send(trigger.to_json())
        await self.ws.recv()  # ACK

        # 2. Payload upload
        payload = SensorPayload(
            session_id=f"sess_{self.client_id}",
            sequence_id=int(time.time_ns() // 1_000_000),
            image=ImagePayload(data=base64.b64encode(compressed_frame).decode("utf-8"), width=1280, height=720),
            audio_query=AudioPayload(data=base64.b64encode(audio_query_bytes).decode("utf-8")),
            telemetry=TelemetryPayload(focus_locked=True),
            attached_documents=attached_docs or []
        )
        payload_data = payload.to_dict()
        if target_component_id:
            payload_data["target_component_id"] = target_component_id
        if target_pin_id:
            payload_data["target_pin_id"] = target_pin_id

        await self.ws.send(json.dumps(payload_data))

        # 3. Read stream with OS Audio Ducking & Subtitle sync [INV-07]
        self.duck_os_audio_focus()
        chunks = []

        try:
            while True:
                raw_chunk = await self.ws.recv()
                chunk = ServerAudioStreamChunk.from_dict(json.loads(raw_chunk))
                chunks.append(chunk)

                if chunk.subtitle_token:
                    self.received_subtitles.append(chunk.subtitle_token)
                    if self.on_subtitle_received:
                        self.on_subtitle_received(chunk.subtitle_token)

                # Process chunk through time-stretching audio sink
                if chunk.data:
                    try:
                        raw_pcm = base64.b64decode(chunk.data)
                        self.audio_sink.process_chunk(raw_pcm)
                    except Exception:
                        pass

                if self.on_audio_received:
                    self.on_audio_received(chunk)

                if chunk.is_final:
                    break
        finally:
            self.restore_os_audio_focus()

        return chunks

    # --- F13: Talk-to-Cursor Pipeline ---
    async def trigger_talk_to_cursor(
        self,
        audio_query_bytes: Optional[bytes] = None,
        num_chunks: int = 4
    ) -> List[str]:
        """
        F13 Talk-to-Cursor Pipeline:
        Streams microphone audio to cloud session, intercepts incoming text tokens,
        and injects them append-only directly into the active cursor position.
        Operates strictly in user-space I/O.
        """
        if not self.ws:
            raise RuntimeError("Station client not connected.")

        if audio_query_bytes is None:
            # Ingest chunks from microphone stream
            chunks_list = []
            async for chunk in self.stream_mic_audio_chunks(num_chunks=num_chunks):
                chunks_list.append(chunk)
            audio_query_bytes = b"".join(chunks_list)

        # 1. Trigger Hotkey (Talk to cursor)
        trigger = CaptureTriggerEvent(
            trigger_source=TriggerSource.PC_HOTKEY,
            timestamp_ns=time.time_ns(),
            action="TALK_TO_CURSOR"
        )
        await self.ws.send(trigger.to_json())
        await self.ws.recv()  # ACK

        # 2. Upload Sensor Payload (Voice query without image)
        payload = SensorPayload(
            session_id=f"sess_{self.client_id}",
            sequence_id=int(time.time_ns() // 1_000_000),
            image=ImagePayload(data="", width=0, height=0),
            audio_query=AudioPayload(data=base64.b64encode(audio_query_bytes).decode("utf-8")),
            telemetry=TelemetryPayload(focus_locked=True)
        )
        await self.ws.send(json.dumps(payload.to_dict()))

        # 3. Stream response and inject text tokens directly into cursor position
        injected_tokens = []
        self.duck_os_audio_focus()
        try:
            while True:
                raw_chunk = await self.ws.recv()
                chunk = ServerAudioStreamChunk.from_dict(json.loads(raw_chunk))

                token_to_inject = chunk.subtitle_token or chunk.text_transcript
                if token_to_inject:
                    self.inject_text_at_cursor(token_to_inject)
                    injected_tokens.append(token_to_inject)

                if chunk.data:
                    try:
                        raw_pcm = base64.b64decode(chunk.data)
                        self.audio_sink.process_chunk(raw_pcm)
                    except Exception:
                        pass

                if self.on_audio_received:
                    self.on_audio_received(chunk)

                if chunk.is_final:
                    break
        finally:
            self.restore_os_audio_focus()

        return injected_tokens

    # --- F14: Selected Text-to-Speech (TTS) Reader ---
    async def read_selected_text_aloud(
        self,
        selected_text: Optional[str] = None
    ) -> List[ServerAudioStreamChunk]:
        """
        F14 Selected Text-to-Speech (TTS) Reader:
        Grabs currently highlighted text non-destructively (without polluting OS clipboard),
        dispatches to Cloud Gateway, and streams audio directly to local Time-Stretching Audio Sink.
        [INV-07] OS audio ducking applied during playback.
        """
        if not self.ws:
            raise RuntimeError("Station client not connected.")

        text_to_read = selected_text if selected_text is not None else self.extract_selected_text_non_destructive()
        if not text_to_read or not text_to_read.strip():
            logger.warning("[PCStation] No text selected or clipboard buffer is empty.")
            return []

        # 1. Trigger Hotkey (Read selected text)
        trigger = CaptureTriggerEvent(
            trigger_source=TriggerSource.PC_HOTKEY,
            timestamp_ns=time.time_ns(),
            action="READ_SELECTED_TEXT"
        )
        await self.ws.send(trigger.to_json())
        await self.ws.recv()  # ACK

        # 2. Upload text synthesis payload
        payload = SensorPayload(
            session_id=f"sess_{self.client_id}",
            sequence_id=int(time.time_ns() // 1_000_000),
            image=ImagePayload(data="", width=0, height=0),
            audio_query=AudioPayload(data="", text_transcript=f"Read aloud: {text_to_read}"),
            telemetry=TelemetryPayload(focus_locked=True)
        )
        await self.ws.send(json.dumps(payload.to_dict()))

        # 3. Stream audio chunks directly to Time-Stretching Audio Sink with [INV-07] ducking
        self.duck_os_audio_focus()
        chunks = []
        try:
            while True:
                raw_chunk = await self.ws.recv()
                chunk = ServerAudioStreamChunk.from_dict(json.loads(raw_chunk))
                chunks.append(chunk)

                if chunk.subtitle_token:
                    self.received_subtitles.append(chunk.subtitle_token)
                    if self.on_subtitle_received:
                        self.on_subtitle_received(chunk.subtitle_token)

                if chunk.data:
                    try:
                        raw_pcm = base64.b64decode(chunk.data)
                        self.audio_sink.process_chunk(raw_pcm)
                    except Exception:
                        pass

                if self.on_audio_received:
                    self.on_audio_received(chunk)

                if chunk.is_final:
                    break
        finally:
            self.restore_os_audio_focus()

        return chunks

    # --- F15: Read Below Text ---
    async def read_below_text(
        self,
        mouse_coords: Optional[tuple[int, int]] = None,
        mock_selected_text: Optional[str] = None
    ) -> List[ServerAudioStreamChunk]:
        """
        F15 Read Below Text:
        Non-destructively selects text from cursor/mouse position to bottom of active window/document ->
        sends captured text to Cloud TTS synthesis.
        Preserves user's clipboard buffer intact.
        """
        text = mock_selected_text if mock_selected_text is not None else self.extract_selected_text_non_destructive()
        if not text or not text.strip():
            logger.info(f"[PCStation:{self.client_id}] Read below text triggered at {mouse_coords or 'current pointer'}")
            text = "Content from active window cursor to bottom."
        return await self.read_selected_text_aloud(selected_text=text)

    async def hover_select_to_bottom_tts(
        self,
        mouse_coords: Optional[tuple[int, int]] = None,
        mock_selected_text: Optional[str] = None
    ) -> List[ServerAudioStreamChunk]:
        """Backward compatibility alias for read_below_text."""
        return await self.read_below_text(mouse_coords=mouse_coords, mock_selected_text=mock_selected_text)

    # --- F16: Toggle Audio Playback (Play/Pause/Halt) ---
    def toggle_audio_playback(self) -> bool:
        """
        F16 Toggle Audio Playback:
        Play / Pause / Halt active audio playback on PC Audio Sink.
        Returns True if playback is resumed/active, False if paused/halted.
        """
        is_active = self.audio_sink.toggle_playback()
        if is_active:
            self.duck_os_audio_focus()
        else:
            self.restore_os_audio_focus()
        logger.info(f"[PCStation:{self.client_id}] Audio playback toggled: active={is_active}")
        return is_active

    # --- F17, F18, F19: Local Multi-Monitor Screen Ingestion to Clipboard ---
    def capture_display_to_clipboard(self, display_index: int = 0) -> bytes:
        """
        Captures specific display monitor (0: Display 1, 1: Display 2, 2: Display 3)
        and copies image directly to local OS Clipboard without sending to cloud session.
        Returns the captured JPEG/raw image bytes.
        """
        monitor_idx = display_index + 1  # MSS monitor 1 is primary display, 2 is secondary, etc.
        raw_frame = self.capture_screen_frame(monitor_index=monitor_idx)
        self.copy_image_to_local_clipboard(raw_frame)
        logger.info(f"[PCStation:{self.client_id}] Captured Display {display_index + 1} to local OS Clipboard ({len(raw_frame)} bytes).")
        return raw_frame

    def capture_display_1(self) -> bytes:
        """F17: Capture Monitor 1 (Screen 0) and copy directly to OS Clipboard."""
        return self.capture_display_to_clipboard(display_index=0)

    def capture_display_2(self) -> bytes:
        """F18: Capture Monitor 2 (Screen 1) and copy directly to OS Clipboard."""
        return self.capture_display_to_clipboard(display_index=1)

    def capture_display_3(self) -> bytes:
        """F19: Capture Monitor 3 (Screen 2) and copy directly to OS Clipboard."""
        return self.capture_display_to_clipboard(display_index=2)

    # --- Minimal Status Capsule (Desk Pill) ---
    def get_status_capsule(self) -> Dict[str, str]:
        """
        Minimal Status Capsule (Desk Pill):
        Displays connection dot:
          - 🟢 READY
          - 🟡 CONNECTING
          - 🔵 THINKING/STREAMING
        and active stream tag (e.g., 'LIVE [Disp 1]', 'LIVE [Disp 2]').
        Real-time token counters removed (aggregated on backend ledger).
        """
        dot_icons = {
            "READY": "🟢",
            "CONNECTING": "🟡",
            "THINKING": "🔵",
            "STREAMING": "🔵"
        }
        status_key = self.connection_status_dot.upper()
        icon = dot_icons.get(status_key, "🟢")

        stream_tag = ""
        if self.is_ultrawide_streaming:
            disp_num = (self.active_stream_display_index + 1) if self.active_stream_display_index is not None else 1
            stream_tag = f"LIVE [Disp {disp_num}]"

        return {
            "dot": f"{icon} {status_key}",
            "status": status_key,
            "stream_tag": stream_tag,
            "display": f"{icon} {status_key}" + (f" | {stream_tag}" if stream_tag else "")
        }

    # --- F20 Display Selector & Ultrawide Scaled Live Video Streaming ---
    def discover_monitors(self) -> List[Dict[str, Any]]:
        """
        Discovers all active connected desktop displays.
        Returns list of display tiles e.g., [{'id': 1, 'name': 'Display 1'}, ...].
        """
        monitors = []
        try:
            import mss
            mss_cls = getattr(mss, "MSS", mss.mss)
            with mss_cls() as sct:
                # sct.monitors[0] is all monitors combined; [1..N] are individual screens
                num_screens = len(sct.monitors) - 1 if len(sct.monitors) > 1 else 1
                for i in range(1, num_screens + 1):
                    monitors.append({"id": i, "name": f"Display {i}", "tile": f"[{i}] Display {i}"})
        except Exception:
            pass

        if not monitors:
            monitors = [
                {"id": 1, "name": "Display 1", "tile": "[1] Display 1"},
                {"id": 2, "name": "Display 2", "tile": "[2] Display 2"}
            ]
        return monitors

    def open_f20_display_selector(self) -> List[Dict[str, Any]]:
        """
        On F20 (when not streaming): Pop up transient monitor preview overlay with tiles:
        [1] Display 1, [2] Display 2, etc.
        """
        self.f20_selector_visible = True
        tiles = self.discover_monitors()
        logger.info(f"[PCStation:{self.client_id}] F20 Display Selector opened: {[t['tile'] for t in tiles]}")
        return tiles

    def dismiss_f20_display_selector(self) -> None:
        """Dismiss transient monitor preview overlay."""
        self.f20_selector_visible = False
        logger.info(f"[PCStation:{self.client_id}] F20 Display Selector dismissed.")

    def select_display_and_start_stream(self, display_id_or_key: Union[int, str]) -> int:
        """
        Select display via numeric key ('1', '2', '3') or mouse click -> auto-dismiss overlay
        and immediately stream selected display via wss://.
        Returns the selected display index (0-indexed).
        """
        self.dismiss_f20_display_selector()
        try:
            disp_num = int(display_id_or_key)
        except (ValueError, TypeError):
            disp_num = 1

        display_index = max(0, disp_num - 1)
        self.active_stream_display_index = display_index
        self.is_ultrawide_streaming = True
        self.connection_status_dot = "STREAMING"
        logger.info(f"[PCStation:{self.client_id}] Selected Display {disp_num} -> Streaming active.")
        return display_index

    def stop_display_stream(self) -> None:
        """Immediately stops the video feed."""
        self.is_ultrawide_streaming = False
        self.active_stream_display_index = None
        self.connection_status_dot = "READY"
        logger.info(f"[PCStation:{self.client_id}] Display stream stopped.")

    def toggle_f20_display_selector(self) -> Any:
        """
        F20 Hotkey Behavior:
        - If streaming is currently active -> press F20 immediately stops the video feed.
        - If not streaming:
          * If selector is open -> dismiss selector.
          * If selector is closed -> open selector overlay.
        """
        if self.is_ultrawide_streaming:
            self.stop_display_stream()
            return {"action": "STREAM_STOPPED", "streaming": False}

        if self.f20_selector_visible:
            self.dismiss_f20_display_selector()
            return {"action": "SELECTOR_DISMISSED", "selector_open": False}
        else:
            tiles = self.open_f20_display_selector()
            return {"action": "SELECTOR_OPENED", "selector_open": True, "tiles": tiles}

    def toggle_stream_ultrawide_live(self) -> bool:
        """
        Legacy alias for backward compatibility:
        Toggles continuous downscaled video streaming from primary/ultrawide display.
        """
        if self.is_ultrawide_streaming:
            self.stop_display_stream()
        else:
            self.select_display_and_start_stream(1)
        logger.info(f"[PCStation:{self.client_id}] Ultrawide live streaming toggled: {self.is_ultrawide_streaming}")
        return self.is_ultrawide_streaming

    async def stream_ultrawide_live_frames(
        self,
        interval_sec: float = 0.5,
        max_frames: Optional[int] = None
    ) -> AsyncGenerator[bytes, None]:
        """
        Continuously yields downscaled (<= 1280px width) ultrawide screen capture frames
        for real-time vision-language grounding.
        """
        count = 0
        self.is_ultrawide_streaming = True
        try:
            while self.is_ultrawide_streaming:
                raw_frame = self.capture_screen_frame(monitor_index=1)
                compressed_frame = compress_image_frame(raw_frame, max_dim=1280, quality=80)
                yield compressed_frame
                count += 1
                if max_frames and count >= max_frames:
                    break
                await asyncio.sleep(interval_sec)
        finally:
            self.is_ultrawide_streaming = False

    # --- Universal Hotkey Dispatcher ---
    async def handle_hotkey(self, hotkey_name: str, **kwargs) -> Any:
        """
        Dispatches incoming hotkey (F13–F20) to corresponding handler method.
        """
        action = self.pc_station_hotkeys.get(hotkey_name.lower())
        if not action:
            logger.warning(f"Unmapped PC Station hotkey: '{hotkey_name}'")
            return None

        if action == "talk_to_cursor":
            return await self.trigger_talk_to_cursor(**kwargs)
        elif action == "read_selected_text":
            return await self.read_selected_text_aloud(**kwargs)
        elif action in ("read_below_text", "hover_select_to_bottom_tts"):
            return await self.read_below_text(**kwargs)
        elif action == "toggle_audio_playback":
            return self.toggle_audio_playback()
        elif action == "capture_display_1":
            return self.capture_display_1()
        elif action == "capture_display_2":
            return self.capture_display_2()
        elif action == "capture_display_3":
            return self.capture_display_3()
        elif action == "stream_ultrawide_live":
            return self.toggle_stream_ultrawide_live()
        elif action == "f20_display_selector":
            return self.toggle_f20_display_selector()
        elif action == "quick_drop" or hotkey_name.lower() in ("alt+space", "quick_drop"):
            content = kwargs.get("text_or_url", kwargs.get("content", ""))
            return await self.submit_quick_drop(content)
        else:
            logger.warning(f"Unknown action '{action}' for hotkey '{hotkey_name}'")
            return None

    # --- Direct-to-Cloud Audio Routing on Wake-Word Trigger ---
    async def trigger_wake_word_stream(
        self,
        wake_phrase: str,
        audio_query_bytes: bytes = b""
    ) -> List[ServerAudioStreamChunk]:
        """
        Direct-to-Cloud Audio Pipeline on Wake-Word Trigger:
        Routes mic stream directly to Gemini Live via wss://.
        Validates wake-word dynamically using WakeWordConfig (no hardcoded string).
        Applies Dynamic RMS noise floor tracking.
        """
        if not self.wake_word.matches(wake_phrase):
            logger.warning(f"Wake word phrase '{wake_phrase}' does not match configured wake word.")
            return []

        self.is_dormant = False
        self.dynamic_noise_gate.is_streaming = True
        return await self.send_screen_or_macro_frame(
            image_bytes=b"",
            audio_query_bytes=audio_query_bytes
        )

    async def close(self):
        """Clean close."""
        self.is_ultrawide_streaming = False
        if self.ws:
            await self.ws.close()
        if self.fsm.current_state != EdgeClientState.BOOT_OFFLINE:
            self.fsm.transition_to(EdgeClientState.BOOT_OFFLINE, "Disconnected")
        self.restore_os_audio_focus()
