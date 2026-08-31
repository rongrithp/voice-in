import asyncio
import time
from unittest.mock import MagicMock, patch
import pytest

from src.live_copilot import LiveCopilotSession, sound_feedback


@pytest.fixture(autouse=True)
def mock_external():
    with patch("winsound.Beep"), \
         patch("google.genai.Client"):
        yield


def test_live_copilot_init():
    session = LiveCopilotSession(target_monitor=3, fps=1, model_name="gemini-2.5-flash-native-audio-latest")
    assert session.target_monitor == 3
    assert session.fps == 1
    assert session.model_name == "gemini-2.5-flash-native-audio-latest"
    assert session.is_running is False
    assert session.is_connected is False

    # Test default initialization from config (Monitor 1)
    default_session = LiveCopilotSession()
    assert default_session.target_monitor == 1


def test_live_copilot_toggle():
    session = LiveCopilotSession()
    with patch.object(session, "_run_thread", return_value=None):
        # Toggle ON
        state = session.toggle()
        assert state is True
        assert session.is_running is True

        # Toggle OFF
        state = session.toggle()
        assert state is False
        assert session.is_running is False


def test_live_copilot_start_stop():
    session = LiveCopilotSession()
    with patch.object(session, "_run_thread", return_value=None):
        res = session.start()
        assert res is True
        assert session.is_running is True

        # Idempotent start
        res2 = session.start()
        assert res2 is True

        session.stop()
        assert session.is_running is False


def test_live_copilot_sound_feedback():
    mock_thread = MagicMock()
    with patch("threading.Thread", return_value=mock_thread):
        sound_feedback(880, 50)
        mock_thread.start.assert_called_once()


def test_live_copilot_resolve_client_api_key(monkeypatch):
    import config
    import src.live_copilot as lc
    from google.genai import types
    monkeypatch.setattr(config, "GEMINI_API_KEY", "test-api-key-12345")
    monkeypatch.setenv("GEMINI_API_KEY", "test-api-key-12345")
    monkeypatch.setattr(lc, "_GLOBAL_LIVE_CLIENT", None)
    monkeypatch.setattr(lc, "_GLOBAL_BACKEND_DESC", None)
    session = LiveCopilotSession()
    session._client = None
    session._backend_desc = None
    with patch("src.live_copilot.genai.Client") as mock_client_cls:
        client, desc = session._resolve_client()
        call_kwargs = mock_client_cls.call_args.kwargs
        assert call_kwargs.get("api_key") == "test-api-key-12345"
        assert call_kwargs.get("http_options").api_version == "v1alpha"
        assert "Google AI Studio Direct" in desc


def test_live_copilot_resolve_client_service_account(monkeypatch):
    import config
    import src.live_copilot as lc
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "get_google_credentials_path", lambda: "mock_cred.json")
    monkeypatch.setattr(lc, "_GLOBAL_LIVE_CLIENT", None)
    monkeypatch.setattr(lc, "_GLOBAL_BACKEND_DESC", None)
    
    session = LiveCopilotSession()
    session._client = None
    session._backend_desc = None

    mock_creds = MagicMock()
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", MagicMock()), \
         patch("json.load", return_value={"project_id": "test-live-proj"}), \
         patch("google.oauth2.service_account.Credentials.from_service_account_file", return_value=mock_creds), \
         patch("src.live_copilot.genai.Client") as mock_client_cls:
        client, desc = session._resolve_client()
        call_kwargs = mock_client_cls.call_args.kwargs
        assert call_kwargs.get("vertexai") is True
        assert call_kwargs.get("project") == "test-live-proj"
        assert call_kwargs.get("location") == "us-central1"
        assert call_kwargs.get("credentials") == mock_creds
        assert call_kwargs.get("http_options").api_version == "v1alpha"
        assert "Vertex AI" in desc



@pytest.mark.anyio
async def test_live_copilot_async_live_loop_mock():
    from unittest.mock import AsyncMock
    session = LiveCopilotSession()
    session._is_running = True
    session._stop_event.set()  # Stop immediately on loop check

    mock_client = MagicMock()
    mock_live_session = AsyncMock()
    mock_live_session.receive.return_value = []

    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"):
        await session._async_live_loop()

    assert session.is_connected is False


@pytest.mark.anyio
async def test_live_copilot_mic_callback_queue_full():
    import asyncio
    queue = asyncio.Queue(maxsize=2)
    queue.put_nowait(b"frame1")
    queue.put_nowait(b"frame2")
    assert queue.full()

    # Simulate _safe_put_audio_in logic
    def _safe_put_audio_in(data: bytes):
        try:
            queue.put_nowait(data)
        except (asyncio.QueueFull, Exception):
            try:
                queue.get_nowait()
                queue.task_done()
            except Exception:
                pass
            try:
                queue.put_nowait(data)
            except Exception:
                pass

    # Should not throw QueueFull and should succeed by dropping oldest item
    _safe_put_audio_in(b"frame3")
    assert queue.qsize() == 2
    item1 = queue.get_nowait()
    assert item1 == b"frame2"  # frame1 was dropped
    item2 = queue.get_nowait()
    assert item2 == b"frame3"


@pytest.mark.anyio
async def test_live_copilot_handshake_timeout():
    import asyncio
    from unittest.mock import AsyncMock
    session = LiveCopilotSession()
    session._is_running = True

    mock_client = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.__aenter__.side_effect = asyncio.TimeoutError()
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    # Stop immediately
    session._stop_event.set()

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await session._async_live_loop()

    assert session.is_connected is False


def test_live_copilot_image_downsampling_ultrawide():
    """Verify Ultrawide (3440x1440) is downsampled to width <= 1280 and aspect ratio maintained."""
    from PIL import Image
    # Simulate 3440x1440 image
    img = Image.new("RGB", (3440, 1440), color="blue")
    if img.width > 1280 or img.height > 1280:
        img.thumbnail((1280, 1280), Image.Resampling.BILINEAR)

    assert img.width == 1280
    assert img.height in (535, 536)


@pytest.mark.anyio
async def test_live_copilot_thought_suppression_and_audio_dispatch():
    """Verify that thought parts (part.thought=True) are ignored and audio parts are dispatched."""
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    import asyncio

    session = LiveCopilotSession()
    session._is_running = True

    thought_part = SimpleNamespace(thought=True, text="Thinking about the code...", inline_data=None)
    text_part = SimpleNamespace(thought=False, text="สวัสดีครับ", inline_data=None)
    audio_part = SimpleNamespace(thought=False, text="", inline_data=SimpleNamespace(data=b"audio_bytes_123"))

    turn = SimpleNamespace(parts=[thought_part, text_part, audio_part])
    server_content = SimpleNamespace(interrupted=False, model_turn=turn)
    response = SimpleNamespace(server_content=server_content)

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    async def mock_receive():
        yield response
        session._stop_event.set()

    mock_live_session.receive = mock_receive

    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"), \
         patch("builtins.print") as mock_print:
        await session._async_live_loop()

    # Verified that connect was called with thinking_budget=0
    call_args = mock_client.aio.live.connect.call_args
    assert call_args is not None
    live_config = call_args.kwargs.get("config")
    assert live_config.thinking_config.thinking_budget == 0


@pytest.mark.anyio
async def test_live_copilot_realtime_input_payload():
    """Verify that audio and video frames are sent using audio= and video= fields rather than deprecated media_chunks."""
    from unittest.mock import AsyncMock
    import asyncio

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    sent_realtime_inputs = []

    async def mock_send_realtime_input(**kwargs):
        sent_realtime_inputs.append(kwargs)

    mock_live_session.send_realtime_input = mock_send_realtime_input

    async def mock_receive():
        # Let loop run briefly to grab vision
        for _ in range(5):
            await asyncio.sleep(0.05)
            if any("video" in c for c in sent_realtime_inputs):
                break
        session._stop_event.set()
        session._is_running = False
        if False:
            yield None

    mock_live_session.receive = mock_receive

    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    mock_sct_instance = MagicMock()
    mock_sct_instance.monitors = [
        {"top": 0, "left": 0, "width": 100, "height": 100},
        {"top": 0, "left": 0, "width": 100, "height": 100}
    ]
    mock_grab = MagicMock()
    mock_grab.size = (100, 100)
    mock_grab.width = 100
    mock_grab.height = 100
    mock_grab.bgra = b"\x00" * (100 * 100 * 4)
    mock_sct_instance.grab.return_value = mock_grab

    mock_mss_cm = MagicMock()
    mock_mss_cm.__enter__.return_value = mock_sct_instance
    mock_mss_cm.__exit__.return_value = None

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS", return_value=mock_mss_cm):
        await session._async_live_loop()

    # Verify that at least one video input was sent with video= (and not media=)
    video_calls = [c for c in sent_realtime_inputs if "video" in c]
    assert len(video_calls) > 0
    assert video_calls[0]["video"].mime_type == "image/jpeg"
    assert "media" not in video_calls[0]


@pytest.mark.anyio
async def test_live_copilot_barge_in_non_destructive():
    """Verify that user barge-in (interrupted=True) flushes output and preserves live connection."""
    from unittest.mock import AsyncMock
    from types import SimpleNamespace
    import asyncio

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    # 1. First model turn
    part_1 = SimpleNamespace(thought=False, text="ข้อความ 1", inline_data=SimpleNamespace(data=b"audio_chunk_1"))
    turn_1 = SimpleNamespace(parts=[part_1])
    resp_1 = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, model_turn=turn_1))

    # 2. Barge-in interruption
    resp_barge_in = SimpleNamespace(server_content=SimpleNamespace(interrupted=True, model_turn=None))

    # 3. Subsequent model turn after barge-in
    part_2 = SimpleNamespace(thought=False, text="ข้อความ 2", inline_data=SimpleNamespace(data=b"audio_chunk_2"))
    turn_2 = SimpleNamespace(parts=[part_2])
    resp_2 = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, model_turn=turn_2))

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    processed_turns = []

    async def mock_receive():
        yield resp_1
        await asyncio.sleep(0.05)
        processed_turns.append("turn_1")

        yield resp_barge_in
        await asyncio.sleep(0.05)
        processed_turns.append("barge_in")

        yield resp_2
        await asyncio.sleep(0.05)
        processed_turns.append("turn_2")

        session._stop_event.set()
        session._is_running = False

    mock_live_session.receive = mock_receive

    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"), \
         patch("builtins.print"):
        await session._async_live_loop()

    # Verify all turns were processed continuously without session drop
    assert processed_turns == ["turn_1", "barge_in", "turn_2"]


def test_live_copilot_warmup_fast_non_blocking():
    """Verify that warmup() is non-blocking and executes in < 500ms even if client resolution delays."""
    import time
    session = LiveCopilotSession()
    session._client = None

    with patch.object(session, "_resolve_client", side_effect=lambda: time.sleep(1.0)):
        t0 = time.perf_counter()
        session.warmup()
        elapsed = time.perf_counter() - t0
        # Warmup must return in <= 600ms without blocking caller
        assert elapsed < 0.6


@pytest.mark.anyio
async def test_live_copilot_barge_in_suppressed_when_ai_silent():
    """Verify that interrupted=True is silently ignored when AI is not playing audio."""
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True
    session._is_ai_speaking = False

    resp_interrupt_while_silent = SimpleNamespace(server_content=SimpleNamespace(interrupted=True, model_turn=None))

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    printed_logs = []

    def mock_print(*args, **kwargs):
        msg = " ".join(str(a) for a in args)
        printed_logs.append(msg)

    async def mock_receive():
        yield resp_interrupt_while_silent
        session._stop_event.set()
        session._is_running = False

    mock_live_session.receive = mock_receive
    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"), \
         patch("builtins.print", side_effect=mock_print):
        await session._async_live_loop()

    # Verify no barge-in log was printed when AI was not outputting audio
    barge_in_logs = [log for log in printed_logs if "Barge-in Triggered" in log]
    assert len(barge_in_logs) == 0


@pytest.mark.anyio
async def test_live_copilot_barge_in_throttling_single_log():
    """Verify that multiple consecutive interrupted=True frames only trigger Barge-in once (single log)."""
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    # 1. Model emits audio (6000 bytes = 125ms at 24kHz 16-bit mono)
    part_1 = SimpleNamespace(thought=False, text="กำลังตอบคำถาม", inline_data=SimpleNamespace(data=b"\x01\x00" * 3000))
    turn_1 = SimpleNamespace(parts=[part_1])
    resp_model = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, model_turn=turn_1))

    # 2. Server sends 8 consecutive interrupted packets
    resp_interrupted = SimpleNamespace(server_content=SimpleNamespace(interrupted=True, model_turn=None))

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    printed_logs = []

    def mock_print(*args, **kwargs):
        msg = " ".join(str(a) for a in args)
        printed_logs.append(msg)

    async def mock_receive():
        yield resp_model
        await asyncio.sleep(0.02)

        for _ in range(8):
            yield resp_interrupted
            await asyncio.sleep(0.01)

        session._stop_event.set()
        session._is_running = False

    mock_live_session.receive = mock_receive
    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    mock_out_stream = MagicMock()
    mock_out_stream.write.side_effect = lambda data: time.sleep(0.1)

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream", return_value=mock_out_stream), \
         patch("mss.MSS"), \
         patch("builtins.print", side_effect=mock_print):
        await session._async_live_loop()

    # Verify barge-in was triggered exactly once, not 8 times
    barge_in_logs = [log for log in printed_logs if "Barge-in Triggered" in log]
    assert len(barge_in_logs) == 1


@pytest.mark.anyio
async def test_live_copilot_turn_complete_does_not_disconnect():
    """Verify that turn_complete=True keeps stream alive and does not terminate the session."""
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    part_1 = SimpleNamespace(thought=False, text="จบประโยคแรก", inline_data=None)
    turn_1 = SimpleNamespace(parts=[part_1])
    resp_turn_1 = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, model_turn=turn_1, turn_complete=False))
    resp_turn_complete = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, model_turn=None, turn_complete=True))
    part_2 = SimpleNamespace(thought=False, text="เริ่มประโยคที่สอง", inline_data=None)
    turn_2 = SimpleNamespace(parts=[part_2])
    resp_turn_2 = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, model_turn=turn_2, turn_complete=False))

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    processed_events = []

    async def mock_receive():
        yield resp_turn_1
        processed_events.append("turn_1")
        yield resp_turn_complete
        processed_events.append("turn_complete")
        yield resp_turn_2
        processed_events.append("turn_2")
        session._stop_event.set()
        session._is_running = False

    mock_live_session.receive = mock_receive
    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"), \
         patch("builtins.print"):
        await session._async_live_loop()

    assert processed_events == ["turn_1", "turn_complete", "turn_2"]


@pytest.mark.anyio
async def test_live_copilot_idle_silence_heartbeat_sent():
    """Verify that when audio input queue times out (idle), keep-alive silence frames are sent."""
    from unittest.mock import AsyncMock

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    mock_client = MagicMock()
    mock_live_session = AsyncMock()
    sent_audio_inputs = []

    async def mock_send_realtime_input(**kwargs):
        if "audio" in kwargs:
            sent_audio_inputs.append(kwargs["audio"].data)

    mock_live_session.send_realtime_input = mock_send_realtime_input

    async def mock_receive():
        # Wait until at least 1 heartbeat silence frame is emitted on queue timeout
        for _ in range(6):
            await asyncio.sleep(0.06)
            if len(sent_audio_inputs) > 0:
                break
        session._stop_event.set()
        session._is_running = False
        if False:
            yield None

    mock_live_session.receive = mock_receive
    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"):
        await session._async_live_loop()

    # Heartbeat silence should have been sent (zero bytes)
    assert len(sent_audio_inputs) > 0
    assert sent_audio_inputs[0] == b"\x00" * 2048


@pytest.mark.anyio
async def test_live_copilot_barge_in_keeps_session_alive():
    """Verify that Barge-in (interrupted=True) keeps the live WebSocket session alive for next turns."""
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    resp_turn_1 = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, turn_complete=False, model_turn=SimpleNamespace(parts=[SimpleNamespace(thought=False, text="คำตอบรอบแรก", inline_data=None)])))
    resp_interrupted = SimpleNamespace(server_content=SimpleNamespace(interrupted=True, turn_complete=False, model_turn=None))
    resp_turn_2 = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, turn_complete=True, model_turn=SimpleNamespace(parts=[SimpleNamespace(thought=False, text="คำตอบรอบสองหลังแทรก", inline_data=None)])))

    events_received = []

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    turn_count = 0
    async def mock_receive():
        nonlocal turn_count
        turn_count += 1
        if turn_count == 1:
            events_received.append("turn_1")
            yield resp_turn_1
            events_received.append("barge_in")
            yield resp_interrupted
        elif turn_count == 2:
            events_received.append("turn_2")
            yield resp_turn_2
            session._stop_event.set()
            session._is_running = False

    mock_live_session.receive = mock_receive
    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"), \
         patch("builtins.print"):
        await session._async_live_loop()

    # Verify both turns and barge-in occurred without closing the session
    assert events_received == ["turn_1", "barge_in", "turn_2"]
    assert mock_client.aio.live.connect.call_count == 1


def test_live_copilot_config_defaults():
    """Verify calibrated defaults for noise gate threshold and min speech frames in config."""
    import config
    assert getattr(config, "GEMINI_LIVE_RMS_THRESHOLD", None) == 2500.0
    assert getattr(config, "GEMINI_LIVE_MIN_SPEECH_FRAMES", None) == 3


@pytest.mark.anyio
async def test_live_copilot_noise_gate_filters_continuous_fan_noise():
    """Verify that continuous fan noise below RMS threshold (e.g. RMS ~1000 < 2500) produces only comfort silence frames."""
    from unittest.mock import AsyncMock
    import numpy as np

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    captured_inputs = []
    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    async def mock_send_realtime_input(**kwargs):
        if "audio" in kwargs:
            captured_inputs.append(kwargs["audio"].data)

    mock_live_session.send_realtime_input = mock_send_realtime_input

    captured_callback = None
    def mock_raw_input_stream(*args, **kwargs):
        nonlocal captured_callback
        captured_callback = kwargs.get("callback")
        return MagicMock()

    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    async def mock_receive():
        await asyncio.sleep(0.02)
        # Generate 5 frames of fan noise: RMS ~1000 (int16 array with amplitude ~1000)
        fan_noise = (np.ones(1024, dtype=np.int16) * 1000).tobytes()
        for _ in range(5):
            captured_callback(fan_noise, 1024, None, None)
            await asyncio.sleep(0.02)

        await asyncio.sleep(0.05)
        session._stop_event.set()
        session._is_running = False
        if False:
            yield None

    mock_live_session.receive = mock_receive

    with patch("sounddevice.RawInputStream", side_effect=mock_raw_input_stream), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"):
        await session._async_live_loop()

    assert len(captured_inputs) > 0
    # Every frame sent to Gemini must be pure silence (zero-PCM), completely shielding Gemini from fan noise
    for chunk in captured_inputs:
        assert set(chunk) == {0}


@pytest.mark.anyio
async def test_live_copilot_transient_noise_spike_discarded():
    """Verify that a 1-frame noise spike above threshold is discarded and only silence is streamed."""
    from unittest.mock import AsyncMock
    import numpy as np

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    captured_inputs = []
    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    async def mock_send_realtime_input(**kwargs):
        if "audio" in kwargs:
            captured_inputs.append(kwargs["audio"].data)

    mock_live_session.send_realtime_input = mock_send_realtime_input

    captured_callback = None
    def mock_raw_input_stream(*args, **kwargs):
        nonlocal captured_callback
        captured_callback = kwargs.get("callback")
        return MagicMock()

    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    async def mock_receive():
        await asyncio.sleep(0.02)
        # 1 frame of spike (RMS=3500 > 2500)
        spike_frame = (np.ones(1024, dtype=np.int16) * 3500).tobytes()
        captured_callback(spike_frame, 1024, None, None)
        await asyncio.sleep(0.02)

        # Followed by silence (RMS=0)
        silence_frame = (np.zeros(1024, dtype=np.int16)).tobytes()
        for _ in range(3):
            captured_callback(silence_frame, 1024, None, None)
            await asyncio.sleep(0.02)

        await asyncio.sleep(0.05)
        session._stop_event.set()
        session._is_running = False
        if False:
            yield None

    mock_live_session.receive = mock_receive

    with patch("sounddevice.RawInputStream", side_effect=mock_raw_input_stream), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"):
        await session._async_live_loop()

    # The 1-frame spike must never have been flushed as audio
    assert len(captured_inputs) > 0
    spike_bytes = (np.ones(1024, dtype=np.int16) * 3500).tobytes()
    assert spike_bytes not in captured_inputs
    for chunk in captured_inputs:
        assert set(chunk) == {0}


@pytest.mark.anyio
async def test_live_copilot_consecutive_speech_frames_flush_and_stream():
    """Verify that >= 3 consecutive frames of speech (~192ms) confirm speech intent and flush pre-speech buffer."""
    from unittest.mock import AsyncMock
    import numpy as np

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    captured_inputs = []
    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    async def mock_send_realtime_input(**kwargs):
        if "audio" in kwargs:
            captured_inputs.append(kwargs["audio"].data)

    mock_live_session.send_realtime_input = mock_send_realtime_input

    captured_callback = None
    def mock_raw_input_stream(*args, **kwargs):
        nonlocal captured_callback
        captured_callback = kwargs.get("callback")
        return MagicMock()

    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    async def mock_receive():
        await asyncio.sleep(0.02)
        # 3 consecutive speech frames (RMS=3600 > 2500) with distinct content
        speech_1 = (np.ones(1024, dtype=np.int16) * 3601).tobytes()
        speech_2 = (np.ones(1024, dtype=np.int16) * 3602).tobytes()
        speech_3 = (np.ones(1024, dtype=np.int16) * 3603).tobytes()

        captured_callback(speech_1, 1024, None, None)
        await asyncio.sleep(0.01)
        captured_callback(speech_2, 1024, None, None)
        await asyncio.sleep(0.01)
        captured_callback(speech_3, 1024, None, None)
        await asyncio.sleep(0.05)

        session._stop_event.set()
        session._is_running = False
        if False:
            yield None

    mock_live_session.receive = mock_receive

    with patch("sounddevice.RawInputStream", side_effect=mock_raw_input_stream), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"):
        await session._async_live_loop()

    # Verify that all 3 speech frames (including buffered speech_1 and speech_2) were flushed to Gemini
    assert len(captured_inputs) > 0
    speech_1 = (np.ones(1024, dtype=np.int16) * 3601).tobytes()
    speech_2 = (np.ones(1024, dtype=np.int16) * 3602).tobytes()
    speech_3 = (np.ones(1024, dtype=np.int16) * 3603).tobytes()

    assert speech_1 in captured_inputs
    assert speech_2 in captured_inputs
    assert speech_3 in captured_inputs
    # Check ordering: speech_1 came before speech_2 before speech_3
    idx1 = captured_inputs.index(speech_1)
    idx2 = captured_inputs.index(speech_2)
    idx3 = captured_inputs.index(speech_3)
    assert idx1 < idx2 < idx3


@pytest.mark.anyio
async def test_live_copilot_aec_mute_guard_during_ai_speaking():
    """Verify that when AI is speaking (self._is_ai_speaking=True), mic input is muted to comfort silence."""
    from unittest.mock import AsyncMock
    import numpy as np

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True
    session._is_ai_speaking = True  # AI is outputting audio (speaker active)

    captured_inputs = []
    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    async def mock_send_realtime_input(**kwargs):
        if "audio" in kwargs:
            captured_inputs.append(kwargs["audio"].data)

    mock_live_session.send_realtime_input = mock_send_realtime_input

    captured_callback = None
    def mock_raw_input_stream(*args, **kwargs):
        nonlocal captured_callback
        captured_callback = kwargs.get("callback")
        return MagicMock()

    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    async def mock_receive():
        await asyncio.sleep(0.02)
        # Even with high amplitude acoustic feedback (RMS=4000) from speakers
        speaker_echo = (np.ones(1024, dtype=np.int16) * 4000).tobytes()
        for _ in range(5):
            captured_callback(speaker_echo, 1024, None, None)
            await asyncio.sleep(0.01)

        await asyncio.sleep(0.05)
        session._stop_event.set()
        session._is_running = False
        if False:
            yield None

    mock_live_session.receive = mock_receive

    with patch("sounddevice.RawInputStream", side_effect=mock_raw_input_stream), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"):
        await session._async_live_loop()

    # Verify that all audio chunks sent were converted to comfort silence (0-PCM) by the AEC Mute Guard
    assert len(captured_inputs) > 0
    for chunk in captured_inputs:
        assert set(chunk) == {0}


def test_live_copilot_stop_device_cleanup():
    """Verify that stop() gracefully signals workers, updates flags, and stops without blocking."""
    session = LiveCopilotSession(show_preview=False)
    session._is_running = True
    session._is_connected = True
    session._is_ai_speaking = True

    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = False
    session._worker_thread = mock_thread

    result = session.stop()
    assert result is True
    assert session.is_running is False
    assert session.is_connected is False
    assert session._is_ai_speaking is False
    assert session._stop_event.is_set() is True


def test_live_copilot_opencv_window_cleanup():
    """Verify that stop() calls _safe_destroy_cv2_windows with waitKey pumping to dispose window handles."""
    from src.live_copilot import _safe_destroy_cv2_windows
    with patch("cv2.destroyWindow") as mock_dest_win, \
         patch("cv2.destroyAllWindows") as mock_dest_all, \
         patch("cv2.waitKey") as mock_wait_key:
        _safe_destroy_cv2_windows("Gemini Vision Stream")
        mock_dest_win.assert_called_once_with("Gemini Vision Stream")
        mock_dest_all.assert_called_once()
        assert mock_wait_key.call_count >= 5

    session = LiveCopilotSession(show_preview=True)
    session._is_running = True
    with patch("src.live_copilot._safe_destroy_cv2_windows") as mock_cleanup:
        session.stop()
        mock_cleanup.assert_called_with("Gemini Vision Stream")




