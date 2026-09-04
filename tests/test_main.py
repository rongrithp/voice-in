import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from main import build_system, run_app

@pytest.mark.asyncio
async def test_build_system_wires_all_modules():
    orchestrator = build_system(ws_endpoint="ws://localhost:8080/test")
    assert orchestrator is not None
    assert orchestrator._fsm is not None
    assert orchestrator._transport is not None
    assert orchestrator._keyboard_hook is not None

@pytest.mark.asyncio
async def test_run_app_lifecycle():
    with patch("main.build_system") as mock_build:
        mock_orchestrator = AsyncMock()
        mock_orchestrator.start.return_value = True
        mock_build.return_value = mock_orchestrator

        # Simulate quick shutdown event
        stop_event = asyncio.Event()
        stop_event.set()

        await run_app(stop_event=stop_event)

        mock_orchestrator.start.assert_awaited_once()
        mock_orchestrator.shutdown.assert_awaited_once()
