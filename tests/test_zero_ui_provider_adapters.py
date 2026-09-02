"""
Unit and Integration Tests for Decoupled Model Provider Adapters in Zero-UI Co-pilot.
Verifies BaseModelProvider contract, GeminiLiveAdapter, OpenSourcePipelineAdapter,
ModelProviderFactory dynamic discovery, and hot-swapping in SessionOrchestrator.
"""

import asyncio
import pytest

from src.zero_ui.contracts import (
    UserRuntimeConfig,
    SensorPayload,
    ImagePayload,
    AudioPayload,
    TelemetryPayload
)
from src.zero_ui.ground_truth import GroundTruthEngine
from src.zero_ui.orchestrator import SessionOrchestrator
from src.zero_ui.providers.base import BaseModelProvider, ModelOutputChunk
from src.zero_ui.providers.gemini_live import GeminiLiveAdapter
from src.zero_ui.providers.open_source_pipeline import OpenSourcePipelineAdapter
from src.zero_ui.providers.factory import ModelProviderFactory


def test_gemini_live_adapter_contract():
    async def _test():
        adapter = GeminiLiveAdapter(model_name="gemini-2.5-flash")
        cfg = UserRuntimeConfig(model_name="gemini-2.5-flash")

        await adapter.connect(cfg, system_instructions="You are an expert co-pilot.")
        assert adapter.is_connected is True

        await adapter.send_text_prompt("Check pin 1 connection")
        await adapter.send_audio_chunk(b"\x00\x01" * 100)
        await adapter.send_image_frame(b"MOCK_JPEG_BYTES")

        chunks = []
        async for chunk in adapter.stream_responses():
            assert isinstance(chunk, ModelOutputChunk)
            assert isinstance(chunk.text_token, str)
            assert chunk.audio_pcm is not None
            chunks.append(chunk)

        assert len(chunks) >= 1
        assert chunks[-1].is_final is True

        await adapter.close()
        assert adapter.is_connected is False

    asyncio.run(_test())


def test_open_source_pipeline_adapter_contract():
    async def _test():
        adapter = OpenSourcePipelineAdapter(
            base_url="http://localhost:11434/v1",
            model_name="llama-3-8b-instruct"
        )
        cfg = UserRuntimeConfig(
            provider_type="open_source",
            model_name="llama-3-8b-instruct",
            openai_base_url="http://localhost:11434/v1"
        )

        await adapter.connect(cfg, system_instructions="Open-source assistant.")
        assert adapter.is_connected is True
        assert adapter.base_url == "http://localhost:11434/v1"

        await adapter.send_text_prompt("Read aloud: Caution, high voltage breaker.")
        chunks = []
        async for chunk in adapter.stream_responses():
            assert isinstance(chunk, ModelOutputChunk)
            chunks.append(chunk)

        assert len(chunks) >= 1
        assert any("Caution" in (c.text_token or "") for c in chunks)
        assert chunks[-1].is_final is True

        await adapter.close()
        assert adapter.is_connected is False

    asyncio.run(_test())


def test_open_source_pipeline_custom_streamer_injection():
    async def _test():
        async def mock_llm_stream(sys_prompt, prompt, img_bytes):
            yield "Local "
            yield "Inference "
            yield "Done."

        def mock_tts(token):
            return token.encode("utf-8") + b"_PCM"

        adapter = OpenSourcePipelineAdapter(
            custom_llm_streamer=mock_llm_stream,
            custom_tts_synthesizer=mock_tts
        )
        cfg = UserRuntimeConfig(provider_type="open_source")
        await adapter.connect(cfg, "System instructions")

        await adapter.send_text_prompt("Hello local model")
        chunks = []
        async for chunk in adapter.stream_responses():
            chunks.append(chunk)

        assert len(chunks) == 3
        assert [c.text_token for c in chunks] == ["Local ", "Inference ", "Done."]
        assert chunks[0].audio_pcm == b"Local _PCM"
        assert chunks[-1].is_final is True

        await adapter.close()

    asyncio.run(_test())


def test_model_provider_factory_hot_swap():
    # 1. Default / Gemini
    cfg_gemini = UserRuntimeConfig(provider_type="gemini")
    p1 = ModelProviderFactory.create_provider(cfg_gemini)
    assert isinstance(p1, GeminiLiveAdapter)

    # 2. Open Source / Local / OpenAI
    cfg_os = UserRuntimeConfig(provider_type="open_source")
    p2 = ModelProviderFactory.create_provider(cfg_os)
    assert isinstance(p2, OpenSourcePipelineAdapter)

    cfg_local = UserRuntimeConfig(provider_type="local")
    p3 = ModelProviderFactory.create_provider(cfg_local)
    assert isinstance(p3, OpenSourcePipelineAdapter)

    # 3. Custom Registered Provider
    class CustomMockProvider(BaseModelProvider):
        async def connect(self, config, system_instructions): pass
        async def send_audio_chunk(self, pcm_data): pass
        async def send_image_frame(self, image_bytes): pass
        async def send_text_prompt(self, text): pass
        async def stream_responses(self):
            yield ModelOutputChunk(text_token="Custom", is_final=True)
        async def close(self): pass

    ModelProviderFactory.register_provider("custom_vllm", CustomMockProvider)
    cfg_custom = UserRuntimeConfig(provider_type="custom_vllm")
    p4 = ModelProviderFactory.create_provider(cfg_custom)
    assert isinstance(p4, CustomMockProvider)


def test_orchestrator_seamless_provider_hot_swapping():
    async def _test():
        engine = GroundTruthEngine(":memory:")

        # 1. Orchestrator configured with OpenSourcePipelineAdapter
        os_adapter = OpenSourcePipelineAdapter()
        orchestrator_os = SessionOrchestrator(
            session_id="sess_os_01",
            project_id="test_proj",
            ground_truth_engine=engine,
            model_provider=os_adapter
        )

        payload = SensorPayload(
            session_id="sess_os_01",
            sequence_id=1,
            image=ImagePayload(data="", width=0, height=0),
            audio_query=AudioPayload(data="", text_transcript="Test query for open source provider"),
            telemetry=TelemetryPayload(focus_locked=True)
        )

        chunks_os = []
        async for chunk in orchestrator_os.process_sensor_payload(payload):
            chunks_os.append(chunk)

        assert len(chunks_os) >= 1
        assert any("Open-Source" in (c.text_transcript or "") for c in chunks_os)

        # 2. Orchestrator configured with GeminiLiveAdapter
        gemini_adapter = GeminiLiveAdapter()
        orchestrator_gemini = SessionOrchestrator(
            session_id="sess_gemini_01",
            project_id="test_proj",
            ground_truth_engine=engine,
            model_provider=gemini_adapter
        )

        chunks_gemini = []
        async for chunk in orchestrator_gemini.process_sensor_payload(payload):
            chunks_gemini.append(chunk)

        assert len(chunks_gemini) >= 1
        assert any("Verified" in (c.text_transcript or "") for c in chunks_gemini)

    asyncio.run(_test())
