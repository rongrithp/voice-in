import pytest
import asyncio
from unittest.mock import MagicMock, patch
from src.screen_capture import ScreenCaptureProvider

@pytest.fixture
def provider():
    return ScreenCaptureProvider(quality=80)

@pytest.mark.asyncio
async def test_capture_returns_valid_jpeg_bytes(provider):
    # Mocking mss capture and PIL encoding
    mock_frame = {"width": 100, "height": 100, "rgb": b"\x00" * 30000}
    
    with patch("mss.mss") as mock_mss_class:
        mock_instance = MagicMock()
        mock_instance.monitors = [{"left": 0, "top": 0, "width": 100, "height": 100}]
        mock_instance.grab.return_value = mock_frame
        mock_mss_class.return_value.__enter__.return_value = mock_instance
        
        with patch("PIL.Image.frombytes") as mock_frombytes:
            mock_img = MagicMock()
            mock_frombytes.return_value = mock_img
            
            def fake_save(stream, format, quality):
                stream.write(b"\xff\xd8\xff\xe0FAKEJPEG")
            
            mock_img.save.side_effect = fake_save
            
            result_bytes = await provider.capture()
            assert result_bytes.startswith(b"\xff\xd8")
            assert len(result_bytes) > 0

@pytest.mark.asyncio
async def test_capture_failure_returns_none_gracefully(provider):
    with patch("mss.mss", side_effect=RuntimeError("Screen capture denied")):
        result = await provider.capture()
        assert result is None
