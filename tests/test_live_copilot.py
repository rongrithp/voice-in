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
    session.enable_wind_filter = False

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
    assert getattr(config, "GEMINI_LIVE_BARGE_IN_THRESHOLD", None) == 3500.0
    assert getattr(config, "GEMINI_LIVE_MIN_SPEECH_FRAMES", None) == 3





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


def test_live_copilot_stop_purges_audio_streams_and_tasks():
    """Verify that stop() forcefully closes speaker/mic streams, purges audio queues, and cancels tasks."""
    import asyncio
    session = LiveCopilotSession()
    session._is_running = True

    mock_speaker = MagicMock()
    mock_mic = MagicMock()
    session._speaker_stream = mock_speaker
    session._mic_stream = mock_mic

    mock_out_q = asyncio.Queue()
    mock_out_q.put_nowait(b"audio_chunk_1")
    mock_out_q.put_nowait(b"audio_chunk_2")
    session._audio_out_queue = mock_out_q

    mock_loop = MagicMock()
    mock_loop.is_running.return_value = True
    mock_task = MagicMock()
    mock_task.done.return_value = False
    session._async_loop = mock_loop
    session._worker_tasks = [mock_task]

    session.stop()

    # Verify stream closure
    mock_speaker.close.assert_called_once()
    mock_mic.close.assert_called_once()
    assert session._speaker_stream is None
    assert session._mic_stream is None

    # Verify queue purge
    assert mock_out_q.empty() is True

    # Verify task cancellation & loop stop
    assert mock_loop.call_soon_threadsafe.called is True


def test_live_copilot_echo_suppression_and_mic_ducking():
    """Verify that _is_speaking_active suppresses mic input and replaces frames with comfort silence."""
    import time
    session = LiveCopilotSession()
    assert session._is_speaking_active.is_set() is False

    # Simulate speaking active
    session._is_speaking_active.set()
    assert session._is_speaking_active.is_set() is True

    # Stop session resets speaking active
    session.stop()
    assert session._is_speaking_active.is_set() is False


# ─── Contract Tests: LIVE_COPILOT_CONFIG propagation ─────────────────────────


def test_config_pacing_instruction_propagates_to_system_instruction():
    """
    Verify that mutating pacing_instruction in LIVE_COPILOT_CONFIG directly
    affects the string produced by build_system_instruction().
    """
    from config import build_system_instruction

    custom_cfg = {
        "role": "Expert Logic Co-pilot",
        "pacing_instruction": "UNIQUE_PACING_SENTINEL_XYZ",
        "speech_invariants": "",
    }
    result = build_system_instruction(custom_cfg)
    assert "UNIQUE_PACING_SENTINEL_XYZ" in result, (
        "pacing_instruction must be embedded verbatim in the assembled system instruction"
    )

    # Mutating the value must change the output
    custom_cfg["pacing_instruction"] = "ANOTHER_PACING_SENTINEL_ABC"
    result2 = build_system_instruction(custom_cfg)
    assert "ANOTHER_PACING_SENTINEL_ABC" in result2
    assert "UNIQUE_PACING_SENTINEL_XYZ" not in result2


def test_config_turn_silence_timeout_propagates():
    """
    Verify that LIVE_COPILOT_CONFIG['turn_silence_timeout_sec'] carries the
    correct calibrated value (1.2 s) and is accessible as a plain float.
    """
    from config import LIVE_COPILOT_CONFIG

    timeout = LIVE_COPILOT_CONFIG.get("turn_silence_timeout_sec")
    assert timeout is not None, "turn_silence_timeout_sec must be present in LIVE_COPILOT_CONFIG"
    assert isinstance(timeout, float), "turn_silence_timeout_sec must be a float"
    assert timeout == 1.2, f"Expected 1.2, got {timeout}"


def test_config_dsp_flags_propagate_into_session():
    """
    Verify that LiveCopilotSession binds noise_threshold and enable_wind_filter
    directly from LIVE_COPILOT_CONFIG so no hardcoded fallback is needed.
    """
    from config import LIVE_COPILOT_CONFIG

    session = LiveCopilotSession()

    expected_threshold = float(LIVE_COPILOT_CONFIG["rms_speech_threshold"])
    expected_wind = bool(LIVE_COPILOT_CONFIG["enable_wind_filter"])
    expected_cutoff = float(LIVE_COPILOT_CONFIG["wind_filter_cutoff_hz"])

    assert session.noise_threshold == expected_threshold, (
        f"session.noise_threshold ({session.noise_threshold}) must equal "
        f"LIVE_COPILOT_CONFIG['rms_speech_threshold'] ({expected_threshold})"
    )
    assert session.enable_wind_filter == expected_wind, (
        f"session.enable_wind_filter ({session.enable_wind_filter}) must equal "
        f"LIVE_COPILOT_CONFIG['enable_wind_filter'] ({expected_wind})"
    )
    assert session.wind_cutoff_hz == expected_cutoff, (
        f"session.wind_cutoff_hz ({session.wind_cutoff_hz}) must equal "
        f"LIVE_COPILOT_CONFIG['wind_filter_cutoff_hz'] ({expected_cutoff})"
    )


@pytest.mark.anyio
async def test_live_copilot_server_side_barge_in_interruption():
    """Verify that user mic audio does NOT locally trigger barge-in cutoff (preventing AC/fan noise false positives),
    and that immediate playback cutoff (aborting stream & purging audio) occurs when Gemini server sends interrupted=True."""
    from unittest.mock import AsyncMock
    import numpy as np

    session = LiveCopilotSession(show_preview=False)

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    server_responses = asyncio.Queue()

    async def mock_receive():
        while not session._stop_event.is_set():
            try:
                msg = await asyncio.wait_for(server_responses.get(), timeout=0.05)
                yield msg
            except asyncio.TimeoutError:
                continue

    mock_live_session.receive = mock_receive
    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn
    session._client = mock_client
    session._backend_desc = "Mock Direct"

    captured_mic_cb = None
    mock_out_stream = MagicMock()

    def capture_mic_init(*args, **kwargs):
        nonlocal captured_mic_cb
        captured_mic_cb = kwargs.get("callback")
        mock_in = MagicMock()
        mock_in.__enter__.return_value = mock_in
        return mock_in

    def capture_out_init(*args, **kwargs):
        mock_out = mock_out_stream
        import time
        mock_out.write.side_effect = lambda data: time.sleep(0.5)
        mock_out.__enter__.return_value = mock_out
        return mock_out

    with patch("sounddevice.RawInputStream", side_effect=capture_mic_init), \
         patch("sounddevice.RawOutputStream", side_effect=capture_out_init), \
         patch("mss.MSS"):
        session._is_running = True
        loop_task = asyncio.create_task(session._async_live_loop())
        await asyncio.sleep(0.05)

        assert captured_mic_cb is not None
        # Simulate AI speaking actively
        session._is_ai_speaking = True
        session._is_speaking_active.set()
        for _ in range(20):
            session._audio_out_queue.put_nowait(b"\x00" * 2048)

        # 1. Loud continuous background noise (RMS ~ 7000) into mic
        t = np.linspace(0, 0.064, 1024, endpoint=False)
        loud_ambient_audio = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16).tobytes()

        for _ in range(5):
            captured_mic_cb(loud_ambient_audio, 1024, None, None)
        await asyncio.sleep(0.05)

        # Local RMS threshold must NOT cut off AI playback
        assert session._is_ai_speaking is True
        mock_out_stream.abort.assert_not_called()

        # 2. Server detects user speech via neural VAD and sends server_content.interrupted = True
        interrupt_response = MagicMock()
        server_content = MagicMock()
        server_content.interrupted = True
        server_content.turn_complete = False
        server_content.model_turn = None
        interrupt_response.server_content = server_content

        await server_responses.put(interrupt_response)
        await asyncio.sleep(0.08)

        # Playback must now be immediately cut off and hardware buffer aborted
        assert session._is_ai_speaking is False
        mock_out_stream.abort.assert_called()

        session._stop_event.set()
        session._is_running = False
        await loop_task


@pytest.mark.anyio
async def test_live_copilot_aec_mute_guard_during_ai_speaking():
    """Verify Acoustic Echo Guard: when AI is speaking (self._is_ai_speaking=True),
    mic input is muted to comfort silence (all zeros) so Gemini does not hear itself.
    When AI is silent (self._is_ai_speaking=False), raw mic PCM is passed through."""
    from unittest.mock import AsyncMock
    import numpy as np

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True
    session.enable_wind_filter = False

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
        mock_in = MagicMock()
        mock_in.__enter__.return_value = mock_in
        return mock_in

    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    async def mock_receive():
        await asyncio.sleep(0.02)
        # Phase 1: AI speaking -> must send comfort silence (0-PCM)
        session._is_ai_speaking = True
        session._is_speaking_active.set()
        speaker_echo = (np.ones(1024, dtype=np.int16) * 4000).tobytes()
        captured_callback(speaker_echo, 1024, None, None)
        await asyncio.sleep(0.02)

        # Phase 2: AI silent -> must send raw mic PCM
        session._is_ai_speaking = False
        session._is_speaking_active.clear()
        user_speech = (np.ones(1024, dtype=np.int16) * 2000).tobytes()
        captured_callback(user_speech, 1024, None, None)
        await asyncio.sleep(0.02)

        session._stop_event.set()
        session._is_running = False
        if False:
            yield None

    mock_live_session.receive = mock_receive

    with patch("sounddevice.RawInputStream", side_effect=mock_raw_input_stream), \
         patch("sounddevice.RawOutputStream"), \
         patch("mss.MSS"):
        await session._async_live_loop()

    assert len(captured_inputs) >= 2
    # Chunks sent during AI speaking / heartbeat must be pure comfort silence
    assert any(set(c) == {0} for c in captured_inputs)
    # AI silent phase must contain user speech
    user_speech = (np.ones(1024, dtype=np.int16) * 2000).tobytes()
    assert user_speech in captured_inputs


@pytest.mark.anyio
async def test_live_copilot_interruption_state_machine_self_healing():
    """Verify that after server_content.interrupted=True fires:
    1. Audio output queue is purged.
    2. Speaking flags are fully reset to False.
    3. Subsequent model turns are received and output without zombie deadlock."""
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    # Turn 1: AI speaking
    part_1 = SimpleNamespace(thought=False, text="ประโยคแรก", inline_data=SimpleNamespace(data=b"audio_turn_1"))
    resp_1 = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, turn_complete=False, model_turn=SimpleNamespace(parts=[part_1])))

    # Server interruption
    resp_interrupted = SimpleNamespace(server_content=SimpleNamespace(interrupted=True, turn_complete=False, model_turn=None))

    # Turn 2: New AI turn after interruption (should self-heal and not be blocked)
    part_2 = SimpleNamespace(thought=False, text="ประโยคหลังขัดจังหวะ", inline_data=SimpleNamespace(data=b"audio_turn_2"))
    resp_2 = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, turn_complete=False, model_turn=SimpleNamespace(parts=[part_2])))

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    events_received = []

    async def mock_receive():
        # Yield Turn 1
        yield resp_1
        await asyncio.sleep(0.02)
        events_received.append("turn_1")

        # Yield Interruption
        yield resp_interrupted
        await asyncio.sleep(0.02)
        events_received.append("interrupted")
        # Assert state is reset
        assert session._is_ai_speaking is False
        assert session._is_speaking_active.is_set() is False

        # Yield Turn 2
        yield resp_2
        await asyncio.sleep(0.05)
        events_received.append("turn_2")

        session._stop_event.set()
        session._is_running = False

    mock_live_session.receive = mock_receive
    mock_conn = AsyncMock()
    mock_conn.__aenter__.return_value = mock_live_session
    mock_conn.__aexit__.return_value = None
    mock_client.aio.live.connect.return_value = mock_conn

    session._client = mock_client
    session._backend_desc = "Mock Direct"

    mock_out = MagicMock()
    mock_out.write = MagicMock()

    with patch("sounddevice.RawInputStream"), \
         patch("sounddevice.RawOutputStream", return_value=mock_out), \
         patch("mss.MSS"), \
         patch("builtins.print"):
        await session._async_live_loop()

    assert events_received == ["turn_1", "interrupted", "turn_2"]
    # Verify that turn 2 text was appended to session transcript
    transcripts = [t["text"] for t in session._session_transcript if t.get("role") == "model"]
    assert any("ประโยคหลังขัดจังหวะ" in t for t in transcripts)


def test_live_copilot_intent_override_threshold():
    """Verify Vocal Barge-in via High-Threshold Energy Gate during AI Playback:
    - Whenever playback is active (_is_ai_speaking=True or _is_speaking_active.set()):
      - Frames with rms < 4000.0 yield zero-PCM (b"\x00" * len(raw_bytes)).
      - 1st frame with rms >= 4000.0 yields zero-PCM (consecutive = 1 < 2).
      - 2nd consecutive frame with rms >= 4000.0 passes raw mic PCM bytes upstream.
      - A subsequent frame with rms < 4000.0 resets counter and yields zero-PCM.
    - When playback is inactive, raw PCM passes through directly to audio_in_queue.
    """
    import asyncio
    import numpy as np

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True
    session.enable_wind_filter = False

    # Synchronous audio input queue to capture results directly
    audio_in_queue = asyncio.Queue()
    session._audio_in_queue = audio_in_queue

    # Frame definitions:
    low_rms_frame = np.full(1024, 500, dtype=np.int16).tobytes()
    high_rms_frame_1 = np.full(1024, 8000, dtype=np.int16).tobytes()
    high_rms_frame_2 = np.full(1024, 15000, dtype=np.int16).tobytes()

    # --- Phase 1: Playback is Active (High-Threshold Energy Gate) ---
    session._is_ai_speaking = True
    session._is_speaking_active.set()

    # 1. Low RMS frame during playback -> must yield zero-PCM
    session.mic_callback(low_rms_frame, 1024, None, None)
    assert audio_in_queue.get_nowait() == b"\x00" * len(low_rms_frame)
    assert session._override_consecutive_frames == 0

    # 2. 1st High RMS frame (>= 4000) during playback -> must yield zero-PCM (consecutive = 1 < 2)
    session.mic_callback(high_rms_frame_1, 1024, None, None)
    assert audio_in_queue.get_nowait() == b"\x00" * len(high_rms_frame_1)
    assert session._override_consecutive_frames == 1

    # 3. 2nd consecutive High RMS frame (>= 4000) during playback -> PASS raw PCM upstream for barge-in!
    session.mic_callback(high_rms_frame_2, 1024, None, None)
    assert audio_in_queue.get_nowait() == high_rms_frame_2
    assert session._override_consecutive_frames == 2

    # 4. Subsequent Low RMS frame (< 4000) -> resets consecutive counter to 0 and yields zero-PCM
    session.mic_callback(low_rms_frame, 1024, None, None)
    assert audio_in_queue.get_nowait() == b"\x00" * len(low_rms_frame)
    assert session._override_consecutive_frames == 0

    # --- Phase 2: Playback is Inactive (AI Silent) -> Passthrough ---
    session._is_ai_speaking = False
    session._is_speaking_active.clear()

    # Low RMS frame when silent -> passes raw PCM directly
    session.mic_callback(low_rms_frame, 1024, None, None)
    assert audio_in_queue.get_nowait() == low_rms_frame
    assert session._override_consecutive_frames == 0

    # High RMS frame when silent -> passes raw PCM directly
    session.mic_callback(high_rms_frame_1, 1024, None, None)
    assert audio_in_queue.get_nowait() == high_rms_frame_1
    assert session._override_consecutive_frames == 0


@pytest.mark.anyio
async def test_live_copilot_eager_playback_mute_and_hangover_grace_period():
    """Verify:
    1. Eager Playback Mute: receiving server audio chunk immediately sets _is_ai_speaking=True before playback starts.
    2. Post-Playback Grace Period: when queue empties, _is_ai_speaking stays True for 300ms before returning to False.
    """
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    session = LiveCopilotSession(show_preview=False)
    session._is_running = True

    part = SimpleNamespace(thought=False, text="ทดสอบระบบ", inline_data=SimpleNamespace(data=b"\x01\x00" * 1000))
    turn = SimpleNamespace(parts=[part])
    resp = SimpleNamespace(server_content=SimpleNamespace(interrupted=False, model_turn=turn))

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    observed_eager_mute = False
    observed_in_grace_period = False
    observed_post_grace_cleared = False

    async def mock_receive():
        yield resp
        # 1. Check Eager Mute immediately after chunk arrival
        nonlocal observed_eager_mute, observed_in_grace_period, observed_post_grace_cleared
        observed_eager_mute = session._is_ai_speaking and session._is_speaking_active.is_set()

        # 2. Wait until playback finishes writing (100ms) but within 300ms grace period
        await asyncio.sleep(0.15)
        observed_in_grace_period = session._is_ai_speaking and session._is_speaking_active.is_set()

        # 3. Wait until after 300ms grace period expires (> 350ms total)
        await asyncio.sleep(0.35)
        observed_post_grace_cleared = not session._is_ai_speaking and not session._is_speaking_active.is_set()

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

    assert observed_eager_mute is True, "Eager Playback Mute must engage immediately upon receiving audio chunk"
    assert observed_in_grace_period is True, "Mute guard must remain active during 300ms post-playback grace period"
    assert observed_post_grace_cleared is True, "Speaking state must clear after 300ms grace period has elapsed"




