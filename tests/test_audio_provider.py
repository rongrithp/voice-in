import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from src.audio_buffer import AudioCaptureBuffer
from src.audio_provider import AudioCaptureProvider

@pytest.fixture
def mock_buffer():
    buf = MagicMock(spec=AudioCaptureBuffer)
    return buf

@pytest.mark.asyncio
async def test_initial_provider_stopped(mock_buffer):
    provider = AudioCaptureProvider(buffer=mock_buffer)
    assert not provider.is_capturing

@pytest.mark.asyncio
async def test_start_and_stop_lifecycle(mock_buffer):
    loop = asyncio.get_running_loop()
    provider = AudioCaptureProvider(buffer=mock_buffer, loop=loop)
    
    with patch("sounddevice.InputStream") as mock_stream_class:
        mock_stream = MagicMock()
        mock_stream_class.return_value = mock_stream
        
        provider.start()
        assert provider.is_capturing
        mock_stream.start.assert_called_once()
        
        provider.stop()
        assert not provider.is_capturing
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()

@pytest.mark.asyncio
async def test_stop_is_idempotent(mock_buffer):
    loop = asyncio.get_running_loop()
    provider = AudioCaptureProvider(buffer=mock_buffer, loop=loop)
    
    # Stopping when not running should not raise
    provider.stop()
    assert not provider.is_capturing

@pytest.mark.asyncio
async def test_audio_callback_routes_chunk_to_buffer(mock_buffer):
    loop = asyncio.get_running_loop()
    provider = AudioCaptureProvider(buffer=mock_buffer, loop=loop)
    
    # Simulate hardware callback sending raw audio bytes
    raw_data = b"\x01\x02\x03\x04"
    provider._audio_callback(indata=raw_data, frames=2, time_info={}, status=MagicMock(bool=lambda: False))
    
    mock_buffer.put_threadsafe.assert_called_once_with(raw_data, loop)

@pytest.mark.asyncio
async def test_hardware_failure_triggers_error_callback(mock_buffer):
    loop = asyncio.get_running_loop()
    mock_on_error = AsyncMock()
    provider = AudioCaptureProvider(buffer=mock_buffer, loop=loop, on_error=mock_on_error)
    
    with patch("sounddevice.InputStream", side_effect=RuntimeError("Device busy")):
        success = provider.start()
        assert not success
        assert not provider.is_capturing
        await asyncio.sleep(0.02)
        mock_on_error.assert_awaited_once()
