import numpy as np
import pytest
from src.audio import calculate_rms, is_silence, AudioBuffer

def test_calculate_rms():
    zero_chunk = np.zeros(1000, dtype=np.int16)
    assert calculate_rms(zero_chunk) == 0.0

    loud_chunk = np.full(1000, 1000, dtype=np.int16)
    assert calculate_rms(loud_chunk) == 1000.0

def test_is_silence():
    quiet_chunk = np.full(1000, 20, dtype=np.int16)
    assert is_silence(quiet_chunk, rms_threshold=50.0) is True

    loud_chunk = np.full(1000, 500, dtype=np.int16)
    assert is_silence(loud_chunk, rms_threshold=50.0) is False


def test_audio_buffer_overflow():
    buffer = AudioBuffer(maxsize=3)
    c1 = np.array([1])
    c2 = np.array([2])
    c3 = np.array([3])
    c4 = np.array([4])

    assert buffer.push(c1) is True
    assert buffer.push(c2) is True
    assert buffer.push(c3) is True

    # Buffer full, c1 should be dropped and c4 added
    assert buffer.push(c4) is True

    item1 = buffer.get(timeout=0.1)
    assert np.array_equal(item1, c2)
    item2 = buffer.get(timeout=0.1)
    assert np.array_equal(item2, c3)
    item3 = buffer.get(timeout=0.1)
    assert np.array_equal(item3, c4)
