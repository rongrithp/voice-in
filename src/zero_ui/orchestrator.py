"""
Session Orchestrator for Zero-UI Real-Time Multimodal Personal Co-pilot.
Glues Ephemeral Working Memory [INV-04], Speculative Parallel Pipeline,
Session File Primacy [INV-06], and Real-Time Audio/Subtitle Streaming [INV-07].
"""

from __future__ import annotations
import asyncio
import base64
import logging
import time
from typing import Optional, AsyncGenerator, Dict, Any, Callable, List, Awaitable
from dataclasses import dataclass, field

from src.zero_ui.contracts import (
    ClientHello,
    ClientMode,
    CaptureTriggerEvent,
    SensorPayload,
    ServerAudioStreamChunk,
    StateUpdateEvent,
    SafetyFlag,
    StepStatus,
    AbortFrame,
    AttachedDocumentPayload,
    UserPersonalizationConfig,
    PersonaConfig,
    UserRuntimeConfig,
    ImagePayload,
    AudioPayload,
    TelemetryPayload
)
from src.zero_ui.ground_truth import GroundTruthEngine
from src.zero_ui.fsm import ServerSessionFSM, ServerSessionState
from src.zero_ui.providers.base import BaseModelProvider, ModelOutputChunk
from src.zero_ui.providers.factory import ModelProviderFactory

logger = logging.getLogger("zero_ui.orchestrator")


@dataclass
class EphemeralMemoryBuffer:
    """
    Tier 1 Storage: Ephemeral Working Memory (In-Memory RAM only).
    Enforces [INV-04]: Live video frames, raw audio PCM, and opt-in telemetry (GPS, Motion, Lux)
    exist strictly in RAM and are NEVER persisted to SQLite/Disk.
    """
    current_image: Optional[ImagePayload] = None
    current_audio: Optional[AudioPayload] = None
    current_telemetry: Optional[TelemetryPayload] = None
    sequence_id: int = 0
    loaded_at_ns: int = 0

    def load_payload(self, payload: SensorPayload) -> None:
        self.current_image = payload.image
        self.current_audio = payload.audio_query
        self.current_telemetry = payload.telemetry
        self.sequence_id = payload.sequence_id
        self.loaded_at_ns = time.time_ns()

    def purge(self) -> None:
        """Purge ephemeral sensory buffers immediately after interaction turn."""
        self.current_image = None
        self.current_audio = None
        self.current_telemetry = None
        self.sequence_id = 0
        self.loaded_at_ns = 0


class SessionOrchestrator:
    """
    Coordinates Speculative Parallel Ingestion, Ephemeral RAM lifecycle [INV-04],
    Session File Primacy [INV-06], and low-latency audio/subtitle streaming [INV-07]
    via pluggable BaseModelProvider implementations.
    """

    def __init__(
        self,
        session_id: str,
        project_id: str,
        ground_truth_engine: GroundTruthEngine,
        llm_engine_handler: Optional[Callable[[str, str, Optional[str]], AsyncGenerator[str, None]]] = None,
        tts_handler: Optional[Callable[[str], AsyncGenerator[bytes, None]]] = None,
        personalization_config: Optional[UserPersonalizationConfig] = None,
        persona_config: Optional[PersonaConfig] = None,
        runtime_config: Optional[UserRuntimeConfig] = None,
        model_provider: Optional[BaseModelProvider] = None,
        turn_persistence_hook: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        ledger: Optional[Any] = None
    ):
        self.session_id = session_id
        self.project_id = project_id
        self.ground_truth = ground_truth_engine
        self.fsm = ServerSessionFSM(session_id, on_state_change=self._on_fsm_transition)
        self.llm_handler = llm_engine_handler
        self.tts_handler = tts_handler
        self.personalization = personalization_config or UserPersonalizationConfig()
        self.runtime_config = runtime_config or UserRuntimeConfig()
        self.persona_config = persona_config or (
            self.runtime_config.persona if self.runtime_config else PersonaConfig()
        )
        self.model_provider = model_provider or (
            None if llm_engine_handler else ModelProviderFactory.create_provider(self.runtime_config)
        )
        self.turn_persistence_hook = turn_persistence_hook
        self.ledger = ledger

        # Tier 1: Ephemeral Working Memory (In-Memory RAM) [INV-04]
        self.ephemeral_buffer = EphemeralMemoryBuffer()

        # Tier 2: Persistent Cloud Memory (Structured Dialogue Turns)
        self.dialogue_history: List[Dict[str, Any]] = []

        # Tier 3: Session Documents Reference Index [INV-06]
        self.session_documents: Dict[str, AttachedDocumentPayload] = {}

        self.client_info: Optional[ClientHello] = None
        self.current_step_id: str = "INIT"
        self.current_step_desc: str = "Ready for multimodal personal co-pilot."

        # [INV-AUTH] Session context: carries BYOK api_key, user email, and entitlement tier forwarded
        # from the server AUTH gate after a valid CLIENT_HELLO handshake.
        self.session_context: Dict[str, Any] = {}

        # Cancellation / Abort control
        self._abort_event = asyncio.Event()
        self._active_speculative_task: Optional[asyncio.Task] = None

    def _on_fsm_transition(self, old_state: ServerSessionState, new_state: ServerSessionState):
        logger.info(f"[Orchestrator:{self.session_id}] FSM {old_state.value} -> {new_state.value}")

    def handle_client_hello(self, hello: ClientHello) -> Dict[str, Any]:
        """Perform initial handshake and arm the session."""
        self.client_info = hello
        self.fsm.transition_to(ServerSessionState.STANDBY_ARMED, "Client connected & armed")
        return {
            "type": "SERVER_READY",
            "session_id": self.session_id,
            "project_id": self.project_id,
            "status": "ARMED"
        }

    async def handle_abort_frame(self, abort: AbortFrame) -> None:
        """
        Handle incoming ABORT_FRAME from edge client when on-device intent matched (<50ms).
        Immediately cancels active speculative cloud LLM processing.
        """
        logger.info(f"[Orchestrator:{self.session_id}] ABORT_FRAME received: {abort.reason}")
        self._abort_event.set()
        if self._active_speculative_task and not self._active_speculative_task.done():
            self._active_speculative_task.cancel()
            try:
                await asyncio.sleep(0)  # Yield control to let cancellation propagate
            except Exception:
                pass
        self.ephemeral_buffer.purge()

    def attach_document(self, doc: AttachedDocumentPayload) -> None:
        """
        Attach document to active session. Enforces [INV-06] Session File Primacy Guard.
        """
        self.session_documents[doc.file_name] = doc
        logger.info(f"[Orchestrator:{self.session_id}] Document '{doc.file_name}' attached (Primacy: {doc.priority_rank})")

    def set_personalization(self, config: UserPersonalizationConfig) -> None:
        """Update decoupled persona and user preferences."""
        self.personalization = config
        logger.info(f"[Orchestrator:{self.session_id}] Personalization updated: {config.persona_name}")

    def set_persona_config(self, config: PersonaConfig) -> None:
        """Update configurable persona settings."""
        self.persona_config = config
        logger.info(f"[Orchestrator:{self.session_id}] PersonaConfig updated: style={config.style}")

    def set_persona_style(self, style: str) -> None:
        """Dynamically update persona style (e.g. via settings or VOICE_IN_PERSONA_STYLE)."""
        self.persona_config.style = style
        logger.info(f"[Orchestrator:{self.session_id}] Persona style set to: {style}")

    def adjust_verbosity(self, new_verbosity: str) -> None:
        """
        Dynamically adjusts response verbosity at runtime based on session state or prompt direction.
        Supported values: 'ULTRA_CONCISE' | 'BALANCED' | 'DETAILED'
        """
        self.personalization.response_verbosity = new_verbosity.upper()
        logger.info(f"[Orchestrator:{self.session_id}] Runtime verbosity dynamically adjusted to: {self.personalization.response_verbosity}")

    def _build_grounded_system_prompt(self) -> str:
        """
        Constructs system prompt combining:
        1. Persona & Tone Directives (including PersonaConfig style)
        2. [INV-06] Session File Primacy Guard (attached documents given highest priority)
        3. Ground Truth Engine Circuit Graph (if hardware project loaded)
        """
        sections = []

        # 1. Acoustic-First Speech Monologue Persona (Project Gemini)
        sections.append(
            f"=== PROJECT GEMINI: ACOUSTIC-FIRST MONOLOGUE DIRECTIVE ===\n"
            f"Persona: {self.personalization.persona_name} (Project Gemini)\n"
            f"Style: {self.persona_config.style}\n"
            f"Tone: {self.personalization.tone_directive}\n"
            f"Language: {self.personalization.language_code}\n"
            f"Verbosity: {self.personalization.response_verbosity}\n"
            f"ACOUSTIC-FIRST MONOLOGUE INVARIANT:\n"
            f"- Persona: Speak strictly as an expert thinking out loud to oneself in natural, fluid spoken monologue.\n"
            f"- Contextual Dynamic Verbosity: Keep quick or factual queries concise and bottom-line first. When the user requests explanations, stories, tutorials, or complex analytical breakdowns, expand naturally to provide in-depth narrative and technical depth without artificial brevity.\n"
            f"- Formatting constraints: Zero tables, zero markdown formatting, zero bulleted or nested lists, zero conversational filler or pleasantries."
        )
        if self.persona_config.custom_system_instruction:
            sections.append(f"Persona Instruction: {self.persona_config.custom_system_instruction}")

        if self.personalization.response_verbosity == "ULTRA_CONCISE":
            sections.append("=== VERBOSITY POLICY ===\nULTRA_CONCISE: Give direct, factual 1-2 sentence answers only. Zero conversational filler, zero pleasantries.")
        elif self.personalization.response_verbosity == "DETAILED":
            sections.append("=== VERBOSITY POLICY ===\nDETAILED: Provide comprehensive, step-by-step explanations.")

        if self.personalization.custom_system_instructions:
            sections.append(f"Instructions: {self.personalization.custom_system_instructions}")

        # 2. [INV-06] Session File Primacy Guard
        if self.session_documents:
            docs_summary = ["=== [INV-06] ATTACHED SESSION DOCUMENTS (PRIMARY GROUND TRUTH) ==="]
            for name, doc in sorted(self.session_documents.items(), key=lambda x: x[1].priority_rank):
                docs_summary.append(f"Document [{name}] (Rank {doc.priority_rank}):\n{doc.content_b64_or_text[:1000]}")
            docs_summary.append("Rule: Information from attached session documents overrules any external web search.")
            sections.append("\n".join(docs_summary))

        # 3. Ground Truth Engine Circuit Graph (if present)
        gt_prompt = self.ground_truth.generate_system_prompt(self.project_id)
        if "ZERO-UI HARDWARE CO-PILOT DETERMINISTIC GROUND TRUTH" in gt_prompt or "GROUND TRUTH" in gt_prompt:
            sections.append(gt_prompt)

        return "\n\n".join(sections)

    async def process_sensor_payload(
        self,
        payload: SensorPayload,
        target_component_id: Optional[str] = None,
        target_pin_id: Optional[str] = None
    ) -> AsyncGenerator[ServerAudioStreamChunk, None]:
        """
        Speculative Parallel Pipeline:
        1. Ingest into Tier 1 Ephemeral Working Memory [INV-04] (In-Memory RAM).
        2. Ingest attached documents into Tier 3 Session Index [INV-06].
        3. Parallel execution: Ground Truth / Safety Evaluation + Early Multimodal Dispatch.
        4. If ABORT_FRAME received (<50ms local intent match) -> Cancel immediately.
        5. If Safety Warning or Low Confidence -> Stream Immediate Probe/Halt Audio.
        6. If Safe -> Dispatch to Multimodal Engine with Grounded Context -> Stream Audio & Subtitles.
        """
        self._abort_event.clear()

        # Ingest attached documents from payload if present ([INV-06])
        if payload.attached_documents:
            for doc in payload.attached_documents:
                self.attach_document(doc)

        # Ingest into Tier 1 Ephemeral Working Memory [INV-04]
        self.ephemeral_buffer.load_payload(payload)

        self.fsm.transition_to(ServerSessionState.INGESTING_SENSORY, f"Ingested sequence #{payload.sequence_id}")
        self.fsm.transition_to(ServerSessionState.SAFETY_GROUND_TRUTH_EVAL, "Evaluating safety interlocks")

        # 1. Deterministic Safety & Ground Truth Evaluation
        focus_ok = payload.telemetry.focus_locked
        if target_component_id and target_pin_id:
            safety_flag, warning_msg = self.ground_truth.evaluate_safety_interlock(
                project_id=self.project_id,
                target_component_id=target_component_id,
                target_pin_id=target_pin_id,
                vision_confidence=1.0 if focus_ok else 0.5,
                focus_locked=focus_ok
            )
        else:
            # General image clarity check
            if not focus_ok:
                safety_flag = SafetyFlag.STOP_PROBE_REQUIRED
                warning_msg = "Camera focus not locked. Please hold steady and re-aim, or probe terminal with multimeter."
            else:
                safety_flag = SafetyFlag.CLEAR
                warning_msg = None

        # Check for client abort early
        if self._abort_event.is_set():
            logger.info(f"[Orchestrator:{self.session_id}] Processing aborted by client before dispatch.")
            self.ephemeral_buffer.purge()
            self.fsm.transition_to(ServerSessionState.AWAITING_PHYSICAL_ACK, "Aborted by client")
            return

        # 2. Handle Interlocks & Halt
        if safety_flag == SafetyFlag.STOP_PROBE_REQUIRED:
            self.fsm.transition_to(ServerSessionState.SAFETY_HALT_PROBE, "Safety Halt: Low confidence or unknown entity")
            chunk = ServerAudioStreamChunk(
                session_id=self.session_id,
                chunk_index=0,
                is_final=True,
                safety_flag=SafetyFlag.STOP_PROBE_REQUIRED,
                text_transcript=warning_msg or "Stop: Clarity insufficient. Verify before proceeding.",
                subtitle_token="[STOP_HALT]",
                data=base64.b64encode(b"\x00" * 320).decode("utf-8")
            )
            yield chunk
            self.ephemeral_buffer.purge()
            self.fsm.transition_to(ServerSessionState.AWAITING_PHYSICAL_ACK, "Awaiting clarity or verification")
            return

        if safety_flag == SafetyFlag.INTERLOCK_WARNING:
            # Emit high-priority verbal warning chunk first
            interlock_chunk = ServerAudioStreamChunk(
                session_id=self.session_id,
                chunk_index=0,
                is_final=False,
                safety_flag=SafetyFlag.INTERLOCK_WARNING,
                text_transcript=warning_msg,
                subtitle_token="[WARNING]",
                data=base64.b64encode(b"\x00" * 320).decode("utf-8")
            )
            yield interlock_chunk

        # 3. Speculative Parallel Stream Generation
        self.fsm.transition_to(ServerSessionState.STREAMING_AUDIO_RESPONSE, "Streaming grounded assistance")
        chunk_idx = 1 if safety_flag == SafetyFlag.INTERLOCK_WARNING else 0

        system_prompt = self._build_grounded_system_prompt()
        full_transcript = []

        try:
            if self.llm_handler:
                async for text_part in self.llm_handler(system_prompt, payload.image.data, payload.audio_query.data):
                    if self._abort_event.is_set():
                        logger.info(f"[Orchestrator:{self.session_id}] Stream aborted mid-generation.")
                        break

                    chunk = ServerAudioStreamChunk(
                        session_id=self.session_id,
                        chunk_index=chunk_idx,
                        is_final=False,
                        safety_flag=safety_flag,
                        text_transcript=text_part,
                        subtitle_token=text_part if self.personalization.enable_live_subtitles else None,
                        data=base64.b64encode(text_part.encode("utf-8")).decode("utf-8")
                    )
                    yield chunk
                    chunk_idx += 1
                    full_transcript.append(text_part)

            elif self.model_provider:
                await self.model_provider.connect(self.runtime_config, system_prompt)

                if payload.audio_query and payload.audio_query.data:
                    try:
                        raw_pcm = base64.b64decode(payload.audio_query.data)
                        await self.model_provider.send_audio_chunk(raw_pcm)
                    except Exception:
                        pass

                if payload.image and payload.image.data:
                    try:
                        raw_img = base64.b64decode(payload.image.data)
                        await self.model_provider.send_image_frame(raw_img)
                    except Exception:
                        pass

                prompt_text = payload.audio_query.text_transcript if (payload.audio_query and payload.audio_query.text_transcript) else ""
                if target_component_id:
                    prompt_text = f"Verify component {target_component_id} pin {target_pin_id}. {prompt_text}".strip()

                if prompt_text:
                    await self.model_provider.send_text_prompt(prompt_text)

                async for out_chunk in self.model_provider.stream_responses():
                    if self._abort_event.is_set():
                        logger.info(f"[Orchestrator:{self.session_id}] Stream aborted mid-generation.")
                        break

                    token_val = out_chunk.text_token or ""
                    audio_b64 = (
                        base64.b64encode(out_chunk.audio_pcm).decode("utf-8")
                        if out_chunk.audio_pcm is not None
                        else base64.b64encode(token_val.encode("utf-8")).decode("utf-8")
                    )
                    chunk = ServerAudioStreamChunk(
                        session_id=self.session_id,
                        chunk_index=chunk_idx,
                        is_final=out_chunk.is_final,
                        safety_flag=safety_flag,
                        text_transcript=token_val,
                        subtitle_token=token_val if self.personalization.enable_live_subtitles else None,
                        data=audio_b64
                    )
                    yield chunk
                    chunk_idx += 1
                    if token_val:
                        full_transcript.append(token_val)

            else:
                default_text = "Verified connection safe. Proceed with wiring." if target_component_id else "I am here. How can I help you?"
                yield ServerAudioStreamChunk(
                    session_id=self.session_id,
                    chunk_index=chunk_idx,
                    is_final=True,
                    safety_flag=safety_flag,
                    text_transcript=default_text,
                    subtitle_token=default_text if self.personalization.enable_live_subtitles else None,
                    data=base64.b64encode(default_text.encode("utf-8")).decode("utf-8")
                )
                full_transcript.append(default_text)
        finally:
            # Enforce 3-Tier Storage Lifecycle:
            # 1. Tier 1 Ephemeral RAM Buffer is purged immediately ([INV-04])
            self.ephemeral_buffer.purge()

            # 2. Tier 2 Persistent Dialogue Turn recording (Structured text only)
            if full_transcript and not self._abort_event.is_set():
                resp_text = "".join(full_transcript)
                turn_record = {
                    "session_id": self.session_id,
                    "sequence_id": payload.sequence_id,
                    "timestamp_ns": time.time_ns(),
                    "response": resp_text,
                    "safety_flag": safety_flag.value
                }
                self.dialogue_history.append(turn_record)
                if self.turn_persistence_hook:
                    try:
                        await self.turn_persistence_hook(turn_record)
                    except Exception as e:
                        logger.warning(f"Failed to execute turn persistence hook: {e}")

                # 3. Monthly Usage & Cost Ledger Accounting (Non-blocking)
                if self.ledger:
                    try:
                        # Estimate input tokens (prompt + attached docs) & output tokens
                        in_tokens = max(10, len(self._build_grounded_system_prompt()) // 4)
                        out_tokens = max(5, len(resp_text) // 4)
                        client_id = self.client_info.client_id if self.client_info else "unknown"
                        self.ledger.record_usage(self.session_id, client_id, in_tokens, out_tokens)
                    except Exception as e:
                        logger.warning(f"Failed recording usage to ledger: {e}")

        self.fsm.transition_to(ServerSessionState.AWAITING_PHYSICAL_ACK, "Completed streaming guidance")
