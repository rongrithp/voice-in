import threading
import time
from unittest.mock import MagicMock, patch
import pytest
from src.audio_service import AudioService, is_audio_driver_error


def test_audio_service_cleanup():
    service = AudioService()
    mock_stream = MagicMock()
    mock_audio = MagicMock()

    service.stream = mock_stream
    service.audio = mock_audio

    assert service.abort_event.is_set() is False

    service.cleanup()

    assert service.abort_event.is_set() is True
    assert service.stream is None
    assert service.audio is None

    mock_stream.stop.assert_called()
    mock_stream.close.assert_called()
    mock_audio.terminate.assert_called()


def test_audio_service_cleanup_with_stop_stream():
    service = AudioService()
    mock_stream = MagicMock()
    mock_stream.stop_stream = MagicMock()

    service.stream = mock_stream
    service.cleanup()

    assert service.abort_event.is_set() is True
    mock_stream.stop_stream.assert_called_once()
    mock_stream.close.assert_called_once()
    assert service.stream is None


def test_audio_service_context_manager():
    mock_stream = MagicMock()
    with AudioService() as service:
        service.stream = mock_stream
        assert service.abort_event.is_set() is False

    assert service.abort_event.is_set() is True
    assert service.stream is None
    mock_stream.close.assert_called()


def test_audio_service_read_chunk_aborted():
    service = AudioService()
    service.abort_event.set()
    assert service.read_chunk(480) is None


def test_audio_service_read_chunk_success():
    service = AudioService()
    mock_stream = MagicMock()
    mock_stream.read_available = 480
    mock_stream.read.return_value = (b"\x00\x00" * 480, False)
    service.stream = mock_stream

    chunk = service.read_chunk(480)
    assert chunk == b"\x00\x00" * 480


def test_audio_service_stream_capture_abort_event():
    service = AudioService()
    mock_stream = MagicMock()
    mock_stream.read_available = 480
    mock_stream.read.return_value = (b"\x00\x00" * 480, False)

    with patch("src.audio_service.sd.RawInputStream", return_value=mock_stream):
        gen = service.stream_capture(frame_samples=480)
        chunk1 = next(gen)
        assert len(chunk1) == 960

        # Setting abort_event should stop generator immediately and cleanup
        service.abort_event.set()
        with pytest.raises(StopIteration):
            next(gen)

    assert service.stream is None


def test_audio_service_is_audio_driver_error():
    assert is_audio_driver_error(RuntimeError("Unanticipated host error -9999")) is True
    assert is_audio_driver_error(OSError("MME error")) is True
    assert is_audio_driver_error(RuntimeError("PyAudio stream closed")) is True
    assert is_audio_driver_error(ValueError("Invalid argument")) is False
