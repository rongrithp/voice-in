"""
Abstract Base Model Provider for Zero-UI Real-Time Multimodal Personal Co-pilot.
Decouples the Core Orchestrator from specific AI Vendors (Gemini, Local LLM, Open-Source Pipelines).
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, AsyncIterable, Dict, Any

from src.zero_ui.contracts import UserRuntimeConfig


@dataclass
class ModelOutputChunk:
    """
    Standardized multimodal output chunk emitted by any model provider adapter.
    """
    audio_pcm: Optional[bytes] = None
    text_token: Optional[str] = None
    is_final: bool = False
    raw_payload: Optional[Dict[str, Any]] = None


class BaseModelProvider(ABC):
    """
    Abstract Model Provider Adapter interface.
    Enforces a strict vendor-agnostic contract for streaming bidirectional audio,
    images, and text queries.
    """

    @abstractmethod
    async def connect(self, config: UserRuntimeConfig, system_instructions: str) -> None:
        """Initialize session connection with the target model backend."""
        pass

    @abstractmethod
    async def send_audio_chunk(self, pcm_data: bytes) -> None:
        """Stream raw audio PCM chunk to the model backend."""
        pass

    @abstractmethod
    async def send_image_frame(self, image_bytes: bytes) -> None:
        """Stream compressed JPEG/WebP image or screen frame to the model backend."""
        pass

    @abstractmethod
    async def send_text_prompt(self, text: str) -> None:
        """Send direct text prompt or user query to the model backend."""
        pass

    @abstractmethod
    async def stream_responses(self) -> AsyncIterable[ModelOutputChunk]:
        """Stream unified audio chunks and text tokens from the model backend."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Cleanly close connection and release backend resources."""
        pass
