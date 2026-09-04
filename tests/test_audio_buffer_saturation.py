import time
import threading
import pytest
from src.audio_buffer import AudioRingBuffer

def test_audio_buffer_saturation_drop_oldest_strict():
    """
    Invariant: When saturated, buffer drops oldest items first.
    Newest incoming audio must always survive.
    """
    capacity = 10
    buffer = AudioRingBuffer(capacity=capacity)

    # Push 25 chunks with distinct markers
    for i in range(25):
        chunk = bytes([i % 256]) * 64
        buffer.put(chunk)

    # Must not exceed capacity
    assert buffer.size() == capacity

    drained = buffer.drain()
    assert len(drained) == capacity

    # First item out should be chunk #15, last should be #24
    assert drained[0] == bytes([15]) * 64
    assert drained[-1] == bytes([24]) * 64


def test_audio_buffer_concurrent_stress_saturation():
    """
    Invariant: High-rate concurrent producer and consumer must maintain
    integrity without race conditions, index errors, or memory growth.
    """
    capacity = 50
    buffer = AudioRingBuffer(capacity=capacity)
    stop_event = threading.Event()
    error_log = []

    def producer():
        seq = 0
        while not stop_event.is_set():
            try:
                buffer.put(bytes([seq % 256]) * 128)
                seq += 1
                time.sleep(0.0005)  # 2000 chunks/sec burst
            except Exception as e:
                error_log.append(e)

    def consumer():
        while not stop_event.is_set():
            try:
                drained = buffer.drain()
                assert len(drained) <= capacity
                time.sleep(0.002)
            except Exception as e:
                error_log.append(e)

    threads = [
        threading.Thread(target=producer),
        threading.Thread(target=consumer)
    ]

    for t in threads:
        t.start()

    time.sleep(1.0)  # Run under pressure for 1 sec
    stop_event.set()

    for t in threads:
        t.join()

    assert len(error_log) == 0, f"Concurrent buffer errors detected: {error_log}"
    assert buffer.size() <= capacity
