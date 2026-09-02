import sys
import os
from unittest.mock import patch, MagicMock
import pytest
from src.single_instance import SingleInstanceGuard


def test_single_instance_guard_acquire_release():
    guard = SingleInstanceGuard(app_id="TestVoiceHub_SingleInstance_1")
    try:
        acquired = guard.acquire()
        assert acquired is True
        assert guard.is_locked is True

        # Second acquisition on same guard returns True
        assert guard.acquire() is True
    finally:
        guard.release()
        assert guard.is_locked is False


def test_single_instance_guard_conflict():
    app_id = "TestVoiceHub_Conflict_Check"
    guard1 = SingleInstanceGuard(app_id=app_id)
    guard2 = SingleInstanceGuard(app_id=app_id)

    try:
        acq1 = guard1.acquire()
        assert acq1 is True

        acq2 = guard2.acquire()
        assert acq2 is False
    finally:
        guard1.release()
        guard2.release()


def test_single_instance_guard_context_manager():
    app_id = "TestVoiceHub_Ctx_Check"
    with SingleInstanceGuard(app_id=app_id) as guard:
        assert guard.is_locked is True

    assert guard.is_locked is False


def test_single_instance_file_lock_fallback():
    guard = SingleInstanceGuard(app_id="TestVoiceHub_Fallback_Check")
    # Simulate Windows named mutex failure to test file lock path
    with patch("ctypes.windll.kernel32.CreateMutexW", side_effect=Exception("Mutex failed")):
        try:
            acq = guard.acquire()
            assert acq is True
            assert guard.is_locked is True
        finally:
            guard.release()
            assert guard.is_locked is False
