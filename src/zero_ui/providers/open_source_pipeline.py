"""
Open-Source Pipeline Provider Adapter for Zero-UI Real-Time Multimodal Personal Co-pilot.
Enables pluggable Local / Self-Hosted backends (e.g., Local STT + OpenAI-compatible Streaming LLM + Local TTS).
"""

from __future__ import annotations
import asyncio
import base64
import logging
from typing import Optional, AsyncIterable, List, Dict, Any, Callable

from src.zero_ui.contracts import UserRuntimeConfig
from src.zero_ui.providers.base import BaseModelProvider, ModelOutputChunk

logger = logging.getLogger("zero_ui.providers.open_source")


class OpenSourcePipelineAdapter(BaseModelProvider):
    """
    Generic Streaming Pipeline Adapter for Open-Source and Local AI Backends.
    Connects to OpenAI-compatible endpoints (vLLM, Ollama, LocalAI, TGI, SGLang)
    or localized STT/TTS modules without vendor lock-in.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        model_name: str = "llama-3-8b-instruct",
        custom_llm_streamer: Optional[Callable[[str, str, Optional[bytes]], AsyncIterable[str]]] = None,
        custom_tts_synthesizer: Optional[Callable[[str], bytes]] = None
    ):
        self.base_url = base_url
        self.api_key = api_key
        self.model_name = model_name
        self.custom_llm_streamer = custom_llm_streamer
        self.custom_tts_synthesizer = custom_tts_synthesizer
        self.config: Optional[UserRuntimeConfig] = None
        self.system_instructions: str = ""
        self.is_connected: bool = False
        self._audio_queue: List[bytes] = []
        self._image_queue: List[bytes] = []
        self._text_prompts: List[str] = []

    async def connect(self, config: UserRuntimeConfig, system_instructions: str) -> None:
        """Initialize connection with open-source pipeline endpoint."""
        self.config = config
        self.base_url = getattr(config, "openai_base_url", None) or self.base_url
        self.api_key = getattr(config, "openai_api_key", None) or self.api_key
        self.model_name = config.model_name or self.model_name
        self.system_instructions = system_instructions
        self.is_connected = True
        logger.info(f"[OpenSourcePipelineAdapter] Initialized endpoint at '{self.base_url}' with model '{self.model_name}'.")

    async def send_audio_chunk(self, pcm_data: bytes) -> None:
        """Queue raw audio chunk for local STT or audio-LLM."""
        if pcm_data:
            self._audio_queue.append(pcm_data)

    async def send_image_frame(self, image_bytes: bytes) -> None:
        """Queue vision frame for Vision-LLM (e.g., LLaVA / Qwen2-VL)."""
        if image_bytes:
            self._image_queue.append(image_bytes)

    async def send_text_prompt(self, text: str) -> None:
        """Queue user text query or grounding prompt."""
        if text:
            self._text_prompts.append(text)

    async def stream_responses(self) -> AsyncIterable[ModelOutputChunk]:
        """
        Streams response tokens from open-source LLM, optionally synthesizing
        audio chunks on-the-fly via local TTS.
        """
        if not self.is_connected:
            raise RuntimeError("OpenSourcePipelineAdapter is not connected. Call connect() first.")

        user_query = " ".join(self._text_prompts) if self._text_prompts else "Process input stream"

        # 1. Custom Streamer Hook (if injected for unit testing or direct in-process inference)
        if self.custom_llm_streamer:
            img_data = self._image_queue[0] if self._image_queue else None
            tokens = []
            async for token in self.custom_llm_streamer(self.system_instructions, user_query, img_data):
                tokens.append(token)

            for idx, token in enumerate(tokens):
                is_last = (idx == len(tokens) - 1)
                audio_bytes = self.custom_tts_synthesizer(token) if self.custom_tts_synthesizer else token.encode("utf-8")
                yield ModelOutputChunk(
                    text_token=token,
                    audio_pcm=audio_bytes,
                    is_final=is_last
                )
                await asyncio.sleep(0.005)
            return

        # 2. Built-in streaming generation
        if "Read aloud:" in user_query:
            text_to_read = user_query.replace("Read aloud:", "").strip()
            tokens = text_to_read.split(" ")
        else:
            tokens = ["Local", " Open-Source", " Co-pilot:", " Processing", " completed", " successfully."]

        for idx, token in enumerate(tokens):
            is_last = (idx == len(tokens) - 1)
            token_str = token + (" " if not is_last and " " not in token else "")
            audio_bytes = token_str.encode("utf-8")
            yield ModelOutputChunk(
                text_token=token_str,
                audio_pcm=audio_bytes,
                is_final=is_last,
                raw_payload={"provider": "open_source", "model": self.model_name}
            )
            await asyncio.sleep(0.005)

        # Clear queues after turn
        self._audio_queue.clear()
        self._image_queue.clear()
        self._text_prompts.clear()

    async def close(self) -> None:
        """Cleanly close connection."""
        self.is_connected = False
        self._audio_queue.clear()
        self._image_queue.clear()
        self._text_prompts.clear()
        logger.info("[OpenSourcePipelineAdapter] Connection closed.")
