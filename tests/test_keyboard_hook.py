import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.keyboard_hook import GlobalKeyboardHook

@pytest.fixture
def mock_controller():
    controller = MagicMock()
    controller.handle_f20_press = AsyncMock()
    return controller

@pytest.mark.asyncio
async def test_hook_routes_f20_to_controller(mock_controller):
    loop = asyncio.get_running_loop()
    hook = GlobalKeyboardHook(target_key="f20", on_press_coro=mock_controller.handle_f20_press, loop=loop)
    
    # Simulate low-level physical key trigger
    hook._on_key_event(key_name="f20")
    
    # Allow scheduled threadsafe coroutine to resolve
    await asyncio.sleep(0.05)
    mock_controller.handle_f20_press.assert_awaited_once()

@pytest.mark.asyncio
async def test_hook_ignores_unrelated_keys(mock_controller):
    loop = asyncio.get_running_loop()
    hook = GlobalKeyboardHook(target_key="f20", on_press_coro=mock_controller.handle_f20_press, loop=loop)
    
    # Simulate pressing enter, a, f19
    hook._on_key_event(key_name="enter")
    hook._on_key_event(key_name="a")
    hook._on_key_event(key_name="f19")
    
    await asyncio.sleep(0.05)
    mock_controller.handle_f20_press.assert_not_awaited()

@pytest.mark.asyncio
async def test_hook_start_and_stop_lifecycle(mock_controller):
    loop = asyncio.get_running_loop()
    hook = GlobalKeyboardHook(target_key="f20", on_press_coro=mock_controller.handle_f20_press, loop=loop)
    
    with patch.object(hook, "_create_native_listener") as mock_listener_factory:
        mock_listener = MagicMock()
        mock_listener_factory.return_value = mock_listener
        
        hook.start()
        assert hook.is_running
        mock_listener.start.assert_called_once()
        
        hook.stop()
        assert not hook.is_running
        mock_listener.stop.assert_called_once()
