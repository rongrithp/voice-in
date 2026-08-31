import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.live_gemini_engine import GeminiLiveStreamSession
from src.router import TranscribeEngine, GeminiTranscribeEngine

def test_gemini_live_session_setup_and_stream():
    import threading
    emitted_tokens = []
    received_event = threading.Event()
    def on_token(t):
        emitted_tokens.append(t)
        received_event.set()

    session = GeminiLiveStreamSession(
        on_token_callback=on_token,
        api_key="fake-test-key",
        model_name="gemini-2.0-flash"
    )

    mock_ws = AsyncMock()
    responses = [
        json.dumps({
            "serverContent": {
                "modelTurn": {
                    "parts": [{"text": "สวัสดีครับ"}]
                }
            }
        }),
        json.dumps({
            "serverContent": {
                "modelTurn": {
                    "parts": [{"text": " ทดสอบ Real-Time Streaming"}]
                }
            }
        })
    ]

    async def mock_recv():
        if responses:
            return responses.pop(0)
        await asyncio.sleep(0.005)
        raise asyncio.CancelledError()

    mock_ws.recv.side_effect = mock_recv
    mock_ws.send = AsyncMock()

    with patch("websockets.connect", return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_ws))):
        session.start(timeout=1.0)
        session.send_audio_chunk(b"\x00\x00" * 1600)
        received_event.wait(timeout=0.2)
        session.stop()

    assert "สวัสดีครับ" in emitted_tokens or " ทดสอบ Real-Time Streaming" in emitted_tokens

def test_transcribe_engine_create_live_session():
    engine = TranscribeEngine(engine_type="gemini-2.5-flash")
    session = engine.create_live_session(on_token_callback=lambda t: None)
    assert session is not None
    assert isinstance(session, GeminiLiveStreamSession)
