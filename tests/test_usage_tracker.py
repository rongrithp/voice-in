import pytest
import os
import json
from pathlib import Path
from unittest.mock import patch
from src.usage_tracker import UsageTracker

def test_usage_tracker_monthly_partition(tmp_path):
    storage_file = tmp_path / "usage_stats.json"
    tracker = UsageTracker(storage_path=str(storage_file))

    current_month = tracker._current_month_key()
    summary = tracker.get_current_month_summary()
    assert summary["month"] == current_month
    assert summary["stt_sec"] == 0.0
    assert summary["tts_chars"] == 0

    # Record STT (60 seconds = 1 minute = $0.016 * 36.00 THB = 0.576 THB)
    tracker.record_stt(60.0)
    month_entry = tracker.data["months"][current_month]
    assert month_entry["stt_audio_seconds"] == 60.0
    assert month_entry["stt_requests"] == 1

    # Record TTS Neural2 (100,000 chars = 0.1 * $16 * 36.00 THB = 57.60 THB)
    tracker.record_tts(100_000, is_neural=True)
    assert month_entry["tts_neural2_chars"] == 100_000
    assert month_entry["tts_requests"] == 1

    # Record TTS Standard (100,000 chars = 0.1 * $4 * 36.00 THB = 14.40 THB)
    tracker.record_tts(100_000, is_neural=False)
    assert month_entry["tts_standard_chars"] == 100_000

    summary = tracker.get_current_month_summary()
    assert summary["stt_min"] == 1.0
    assert summary["tts_chars"] == 200_000
    assert abs(summary["stt_cost_thb"] - 0.576) < 1e-3
    assert abs(summary["tts_cost_thb"] - 72.00) < 1e-3
    assert abs(summary["total_cost_thb"] - 72.576) < 1e-3

    # Test persistence reload
    tracker_reloaded = UsageTracker(storage_path=str(storage_file))
    assert current_month in tracker_reloaded.data["months"]
    assert tracker_reloaded.data["months"][current_month]["stt_audio_seconds"] == 60.0

def test_usage_tracker_month_rollover(tmp_path):
    storage_file = tmp_path / "usage_stats.json"
    tracker = UsageTracker(storage_path=str(storage_file))

    # Month 1: 2026-08
    with patch.object(tracker, "_current_month_key", return_value="2026-08"):
        tracker.record_stt(120.0)
        tracker.record_tts(5000, is_neural=True)
        summary_aug = tracker.get_current_month_summary()
        assert summary_aug["month"] == "2026-08"
        assert summary_aug["stt_sec"] == 120.0
        assert summary_aug["tts_chars"] == 5000

    # Month 2: 2026-09 (Rollover)
    with patch.object(tracker, "_current_month_key", return_value="2026-09"):
        summary_sep = tracker.get_current_month_summary()
        assert summary_sep["month"] == "2026-09"
        assert summary_sep["stt_sec"] == 0.0
        assert summary_sep["tts_chars"] == 0

        # Record in new month
        tracker.record_stt(30.0)
        assert tracker.data["months"]["2026-09"]["stt_audio_seconds"] == 30.0

    # Verify 2026-08 data remains intact and unchanged
    assert tracker.data["months"]["2026-08"]["stt_audio_seconds"] == 120.0
    assert tracker.data["months"]["2026-08"]["tts_neural2_chars"] == 5000

def test_usage_tracker_reset(tmp_path):
    storage_file = tmp_path / "usage_stats.json"
    tracker = UsageTracker(storage_path=str(storage_file))
    current_month = tracker._current_month_key()

    tracker.record_stt(60.0)
    tracker.record_tts(1000, is_neural=True)
    assert tracker.data["months"][current_month]["stt_requests"] == 1

    tracker.reset_stats()
    assert tracker.data["months"][current_month]["stt_audio_seconds"] == 0.0
    assert tracker.data["months"][current_month]["tts_neural2_chars"] == 0
