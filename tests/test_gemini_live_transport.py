import json
import pytest
from unittest.mock import AsyncMock, patch
from src.transport import WebSocketTransport

@pytest.mark.asyncio
async def test_gemini_live_handshake_setup_message():
    """
    Invariant: On connect, transport must immediately send setup message
    with correct model, audio modality, and voice config.
    """
    transport = WebSocketTransport()
    mock_ws = AsyncMock()
    transport._ws = mock_ws
    transport._is_connected = True

    api_key = "AIzaSy_TEST_KEY"
    model = "models/gemini-2.0-flash-exp"

    await transport.send_gemini_setup(api_key=api_key, model=model, voice_name="Puck")

    mock_ws.send.assert_called_once()
    sent_payload = json.loads(mock_ws.send.call_args[0][0])

    assert "setup" in sent_payload
    setup = sent_payload["setup"]
    assert setup["model"] == model
    assert "AUDIO" in setup["generation_config"]["response_modalities"]


@pytest.mark.asyncio
async def test_send_realtime_audio_chunk_format():
    """
    Invariant: Realtime audio chunk must wrap 16kHz PCM in base64
    inside realtime_input schema.
    """
    transport = WebSocketTransport()
    mock_ws = AsyncMock()
    transport._ws = mock_ws
    transport._is_connected = True

    pcm_bytes = b"\x00\x01" * 320  # 640 bytes (20ms @ 16kHz 16-bit)
    await transport.send_audio_chunk(pcm_bytes)

    mock_ws.send.assert_called_once()
    payload = json.loads(mock_ws.send.call_args[0][0])

    assert "realtime_input" in payload
    chunk = payload["realtime_input"]["media_chunks"][0]
    assert chunk["mime_type"] == "audio/pcm;rate=16000"
    assert "data" in chunk


@pytest.mark.asyncio
async def test_receive_loop_routes_server_audio_and_interruption():
    """
    Invariant: Receiving inline audio routes PCM to audio callback,
    and 'interrupted': true routes to interruption callback.
    """
    transport = WebSocketTransport()
    audio_chunks_received = []
    interrupted_events = []

    transport.set_audio_callback(lambda data: audio_chunks_received.append(data))
    transport.set_interrupted_callback(lambda: interrupted_events.append(True))

    # 1. Simulate server audio turn
    audio_msg = json.dumps({
        "serverContent": {
            "modelTurn": {
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": "audio/pcm;rate=24000",
                            "data": "AAAA"  # base64
                        }
                    }
                ]
            }
        }
    })
    await transport._handle_incoming_text(audio_msg)
    assert len(audio_chunks_received) == 1

    # 2. Simulate server-side barge-in signal
    interrupt_msg = json.dumps({
        "serverContent": {
            "interrupted": True
        }
    })
    await transport._handle_incoming_text(interrupt_msg)
    assert len(interrupted_events) == 1
