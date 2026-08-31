import os
import json
import tempfile
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from src.live_memory import LiveSessionMemory
from src.live_copilot import LiveCopilotSession


@pytest.fixture
def temp_memory_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        mem_path = os.path.join(tmpdir, "live_memory_test.json")
        yield mem_path


def test_live_memory_init_and_empty(temp_memory_file):
    mem = LiveSessionMemory(memory_file=temp_memory_file)
    assert mem.load_sessions() == []
    assert mem.get_rolling_context() == ""


def test_live_memory_save_session_snapshot(temp_memory_file):
    mem = LiveSessionMemory(memory_file=temp_memory_file)
    turns = [
        {"role": "user", "text": "ช่วยดู error ใน log หน่อยครับ"},
        {"role": "model", "text": "พบ Error 1007 WebSocket connection drop ใน live_copilot.py ครับ"}
    ]
    saved = mem.save_session_snapshot(turns, summary="ตรวจสอบ Error 1007 ใน live_copilot")
    assert saved is True

    sessions = mem.load_sessions()
    assert len(sessions) == 1
    assert sessions[0]["summary"] == "ตรวจสอบ Error 1007 ใน live_copilot"
    assert len(sessions[0]["turns"]) == 2
    assert "User: ช่วยดู error" in sessions[0]["turns"][0]
    assert "Co-pilot: พบ Error 1007" in sessions[0]["turns"][1]
    assert "timestamp" in sessions[0]


def test_live_memory_rolling_window_limit(temp_memory_file):
    mem = LiveSessionMemory(memory_file=temp_memory_file, max_stored_sessions=3)
    for i in range(5):
        mem.save_session_snapshot(f"Session {i} discussion content", summary=f"Summary {i}")

    sessions = mem.load_sessions()
    assert len(sessions) == 3
    assert sessions[0]["summary"] == "Summary 2"
    assert sessions[1]["summary"] == "Summary 3"
    assert sessions[2]["summary"] == "Summary 4"


def test_live_memory_get_rolling_context_formatting(temp_memory_file):
    mem = LiveSessionMemory(memory_file=temp_memory_file)
    mem.save_session_snapshot(
        [{"role": "model", "text": "งานแรก: Debugging barge-in"}],
        summary="งานแรก: แก้ไข Barge-in"
    )
    mem.save_session_snapshot(
        [{"role": "model", "text": "งานที่สอง: Memory integration"}],
        summary="งานที่สอง: เพิ่มระบบ Memory"
    )

    context = mem.get_rolling_context(max_sessions=2)
    assert "Session 1" in context
    assert "แก้ไข Barge-in" in context
    assert "Session 2" in context
    assert "เพิ่มระบบ Memory" in context


def test_live_memory_clear(temp_memory_file):
    mem = LiveSessionMemory(memory_file=temp_memory_file)
    mem.save_session_snapshot("Test session text")
    assert len(mem.load_sessions()) == 1

    cleared = mem.clear_memory()
    assert cleared is True
    assert mem.load_sessions() == []


@pytest.mark.anyio
async def test_live_copilot_hydrates_system_instruction_with_memory(temp_memory_file):
    """Verify that LiveCopilotSession injects rolling context into system instruction."""
    mem = LiveSessionMemory(memory_file=temp_memory_file)
    mem.save_session_snapshot(
        [{"role": "model", "text": "บริบทงานก่อนหน้า: วางแผน Refactor DB"}],
        summary="วางแผน Refactor DB"
    )

    session = LiveCopilotSession()
    session.memory = mem
    session._is_running = True

    mock_client = MagicMock()
    mock_live_session = AsyncMock()

    async def mock_receive():
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

    call_args = mock_client.aio.live.connect.call_args
    assert call_args is not None
    live_config = call_args.kwargs.get("config")
    sys_instruction = live_config.system_instruction.parts[0].text

    assert "Previous Context Summary" in sys_instruction
    assert "วางแผน Refactor DB" in sys_instruction


def test_live_copilot_flushes_memory_snapshot_on_stop(temp_memory_file):
    """Verify that stop() saves in-memory transcript turns to persistent memory store."""
    mem = LiveSessionMemory(memory_file=temp_memory_file)
    session = LiveCopilotSession()
    session.memory = mem
    session._session_transcript = [
        {"role": "user", "text": "บันทึกเรื่อง memory ด้วยนะ"},
        {"role": "model", "text": "รับทราบครับ บันทึกเรียบร้อย"}
    ]

    session.stop()
    sessions = mem.load_sessions()
    assert len(sessions) == 1
    assert "User: บันทึกเรื่อง memory" in sessions[0]["turns"][0]
    assert "Co-pilot: รับทราบครับ" in sessions[0]["turns"][1]
