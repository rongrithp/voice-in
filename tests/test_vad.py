import numpy as np
import pytest
from unittest.mock import MagicMock, patch
import config
from src.vad import WebRTCVADSegmenter

def test_vad_segmenter_initialization():
    segmenter = WebRTCVADSegmenter(sample_rate=16000, frame_duration_ms=30, silence_cutoff_ms=500, mode=3, min_speech_duration_ms=500)
    assert segmenter.sample_rate == 16000
    assert segmenter.frame_duration_ms == 30
    assert segmenter.silence_cutoff_ms == 500
    assert segmenter.min_speech_duration_ms == 500
    assert segmenter.frame_size == 480
    assert segmenter.frame_bytes_len == 960

def test_vad_segmenter_silence_cutoff_and_min_duration():
    segmenter = WebRTCVADSegmenter(sample_rate=16000, frame_duration_ms=30, silence_cutoff_ms=500, mode=3, min_speech_duration_ms=500)

    with patch.object(segmenter.vad, "is_speech") as mock_is_speech:
        segmenter.triggered = True
        # Provide 20 frames of speech (20 * 30ms = 600ms >= 500ms min duration)
        segmenter.voiced_frames = [b"\x00" * 960] * 20

        # Feed 16 silent frames (16 * 30ms = 480ms < 500ms silence limit) -> Not cut yet
        mock_is_speech.return_value = False
        pcm_480ms = b"\x00" * (960 * 16)
        segments = segmenter.process_pcm_chunk(pcm_480ms)
        assert len(segments) == 0

        # Feed 1 more silent frame (now total 510ms >= 500ms cutoff) -> Cut segment!
        pcm_30ms = b"\x00" * 960
        segments = segmenter.process_pcm_chunk(pcm_30ms)
        assert len(segments) == 1
        assert isinstance(segments[0], np.ndarray)
        assert segmenter.triggered is False

def test_vad_segmenter_discards_short_speech():
    segmenter = WebRTCVADSegmenter(sample_rate=16000, frame_duration_ms=30, silence_cutoff_ms=500, mode=3, min_speech_duration_ms=500)

    with patch.object(segmenter.vad, "is_speech") as mock_is_speech:
        segmenter.triggered = True
        # Provide only 5 frames of speech (5 * 30ms = 150ms < 500ms min duration)
        segmenter.voiced_frames = [b"\x00" * 960] * 5

        # Feed 17 silent frames (510ms >= 500ms silence cutoff)
        mock_is_speech.return_value = False
        pcm_510ms = b"\x00" * (960 * 17)
        segments = segmenter.process_pcm_chunk(pcm_510ms)
        # Should be discarded because 150ms < 500ms min duration
        assert len(segments) == 0

def test_vad_segmenter_emit_partial():
    segmenter = WebRTCVADSegmenter(sample_rate=16000, frame_duration_ms=30, silence_cutoff_ms=500, mode=3, min_speech_duration_ms=250)

    with patch.object(segmenter.vad, "is_speech") as mock_is_speech:
        segmenter.triggered = True
        # Simulate active speech frames
        mock_is_speech.return_value = True

        # Feed 10 frames (300ms) with emit_partial=True (partial_interval_ms=300)
        pcm_300ms = b"\x00" * (960 * 10)
        segments = segmenter.process_pcm_chunk(pcm_300ms, emit_partial=True, partial_interval_ms=300)

        # Slices should be yielded immediately without waiting for silence cutoff
        assert len(segments) == 1
        assert isinstance(segments[0], np.ndarray)
