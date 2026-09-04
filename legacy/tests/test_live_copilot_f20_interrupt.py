import time
import pytest
import threading
from unittest.mock import MagicMock, patch

import sys
import os

MODULE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "windows-edge")
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from live_copilot_fsm import LiveCopilotFSM, CopilotState, run_f20_toggle_verification
from hud_overlay import HUDOverlayWindow, hide_overlay, get_default_text_for_mode


def test_f20_interrupt_simulation_exit_0():
    """Simulates: Trigger playback (SPEAKING) -> Verify HUD shows [SPEAKING] -> Press F20 -> Audio stops <50ms -> HUD transitions to [LISTENING]."""
    assert run_f20_toggle_verification() is True


def test_hud_overlay_visual_states():
    """Verify canonical text and modes for all FSM visual indicators."""
    assert get_default_text_for_mode("LISTENING") == "[LISTENING] กำลังฟังคำสั่ง...."
    assert get_default_text_for_mode("THINKING") == "[THINKING] กำลังประมวลผล...."
    assert get_default_text_for_mode("SPEAKING") == "[SPEAKING] กำลังตอบกลับ (กด F20 เพื่อขัดจังหวะ)."
    assert get_default_text_for_mode("STANDBY") == ""


def test_half_duplex_hardware_isolation():
    """Verify that microphone is strictly muted during SPEAKING and un-muted during LISTENING."""
    from audio_recorder import UnifiedAudioStream
    stream = UnifiedAudioStream.get_instance()

    fsm = LiveCopilotFSM()
    assert fsm.state == CopilotState.STANDBY

    # Transition to LISTENING -> mic un-muted
    fsm._set_state(CopilotState.LISTENING)
    assert stream.is_muted is False

    # Transition to SPEAKING -> mic muted
    fsm._set_state(CopilotState.SPEAKING)
    assert stream.is_muted is True

    # Press F20 interrupt -> mic immediately un-muted, state back to LISTENING
    interrupted = fsm.interrupt_speaking()
    assert interrupted == CopilotState.LISTENING
    assert stream.is_muted is False

    # Rollback to STANDBY
    fsm._set_state(CopilotState.STANDBY)
    hide_overlay()


def test_center_subtitle_box_and_stream_handler():
    """Verify show_response_box, update_subtitle, hide_response_box, and FSM _handle_gemini_stream."""
    from hud_overlay import show_response_box, hide_response_box, update_subtitle
    from live_copilot_fsm import LiveCopilotFSM, CopilotState

    fsm = LiveCopilotFSM()
    fsm._set_state(CopilotState.SPEAKING)
    # Drain initial state change HUD event
    while not fsm._hud_queue.empty():
        try:
            fsm._hud_queue.get_nowait()
        except Exception:
            break

    # Test _handle_gemini_stream stripping targets and pushing to queue
    test_stream_chunk = "นี่คือข้อความทดสอบ Subtitle Stream <<TARGET: [100, 200, 300, 400]>>"
    fsm._handle_gemini_stream(test_stream_chunk)

    assert not fsm._hud_queue.empty()
    task = fsm._hud_queue.get_nowait()
    assert task[0] == "SPEAKING"
    assert "นี่คือข้อความทดสอบ Subtitle Stream" in task[2]
    assert "<<TARGET:" not in task[2]

    # Test module-level API helpers execution
    show_response_box("สวัสดีครับ")
    update_subtitle("กำลังตอบกลับ...")
    hide_response_box()
    hide_overlay()


def test_double_click_dismiss_and_reawaken_cycle():
    """
    Verify complete cycle:
    1. Press F20 -> Transitions to LISTENING
    2. Double-Click F20 (<350ms) -> Clean Dismiss -> Transitions to STANDBY
    3. Verify audio recording stopped and HUD hidden
    4. Press F20 again -> Re-awakens cleanly -> Transitions to LISTENING with fresh session ready
    """
    mock_session = MagicMock()
    fsm = LiveCopilotFSM(session_factory=lambda: mock_session)
    assert fsm.state == CopilotState.STANDBY

    # 1. First F20 Press -> LISTENING
    s1 = fsm.on_f20_down()
    assert s1 == CopilotState.LISTENING
    assert fsm.is_listening is True

    # 2. Simulate rapid double-click within 80ms (<350ms) -> Clean Dismiss to STANDBY
    fsm._last_f20_press_time = time.perf_counter() - 0.08
    s2 = fsm.on_f20_down()
    assert s2 == CopilotState.STANDBY
    assert fsm.is_standby is True
    assert fsm._stop_event.is_set()

    # 3. Simulate press after interval > 350ms -> Re-awaken to LISTENING
    fsm._last_f20_press_time = time.perf_counter() - 1.0
    s3 = fsm.on_f20_down()
    assert s3 == CopilotState.LISTENING
    assert fsm.is_listening is True
    assert not fsm._stop_event.is_set()
    assert fsm.active_session is not None

    # Clean up back to STANDBY
    fsm._transition_to_standby(reason="Test Complete")
    hide_overlay()


