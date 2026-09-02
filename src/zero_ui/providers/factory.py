"""
Model Provider Factory for Zero-UI Real-Time Multimodal Personal Co-pilot.
Enables dynamic discovery and instantiation of Model Provider Adapters.
"""

from __future__ import annotations
import logging
from typing import Dict, Type, Optional

from src.zero_ui.contracts import UserRuntimeConfig
from src.zero_ui.providers.base import BaseModelProvider
from src.zero_ui.providers.gemini_live import GeminiLiveAdapter
from src.zero_ui.providers.open_source_pipeline import OpenSourcePipelineAdapter

logger = logging.getLogger("zero_ui.providers.factory")


class ModelProviderFactory:
    """
    Factory creating concrete BaseModelProvider implementations based on configuration.
    """

    _registry: Dict[str, Type[BaseModelProvider]] = {
        "gemini": GeminiLiveAdapter,
        "gemini_live": GeminiLiveAdapter,
        "open_source": OpenSourcePipelineAdapter,
        "opensource": OpenSourcePipelineAdapter,
        "local": OpenSourcePipelineAdapter,
        "openai": OpenSourcePipelineAdapter
    }

    @classmethod
    def register_provider(cls, name: str, provider_cls: Type[BaseModelProvider]) -> None:
        """Register a new custom provider adapter class."""
        cls._registry[name.lower()] = provider_cls
        logger.info(f"Registered model provider adapter: '{name}' -> {provider_cls.__name__}")

    @classmethod
    def create_provider(cls, config: Optional[UserRuntimeConfig] = None) -> BaseModelProvider:
        """
        Creates and returns a BaseModelProvider instance based on config.provider_type.
        Defaults to GeminiLiveAdapter if not specified.
        """
        config = config or UserRuntimeConfig()
        provider_type = getattr(config, "provider_type", "gemini").lower()

        provider_cls = cls._registry.get(provider_type)
        if not provider_cls:
            logger.warning(
                f"Unknown provider_type '{provider_type}', falling back to GeminiLiveAdapter."
            )
            provider_cls = GeminiLiveAdapter

        instance = provider_cls()
        logger.info(f"Created model provider instance: {instance.__class__.__name__} for type '{provider_type}'")
        return instance
