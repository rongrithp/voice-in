import pytest
import asyncio
from src.audio_buffer import AudioCaptureBuffer

@pytest.fixture
def buffer():
    return AudioCaptureBuffer(max_chunks=5)

@pytest.mark.asyncio
async def test_initial_state_empty(buffer):
    assert buffer.is_empty
    assert buffer.chunk_count == 0

@pytest.mark.asyncio
async def test_put_and_get_single_chunk(buffer):
    chunk = b"\x00\x01" * 1024
    await buffer.put(chunk)
    
    assert not buffer.is_empty
    assert buffer.chunk_count == 1
    
    retrieved = await buffer.get()
    assert retrieved == chunk
    assert buffer.is_empty

@pytest.mark.asyncio
async def test_bounded_capacity_drops_oldest(buffer):
    # Buffer max_chunks = 5
    for i in range(5):
        await buffer.put(bytes([i]))
    
    assert buffer.chunk_count == 5
    
    # 6th chunk pushed: must drop oldest (bytes([0]))
    await buffer.put(b"\x99")
    assert buffer.chunk_count == 5
    
    first_out = await buffer.get()
    assert first_out == bytes([1])  # bytes([0]) was dropped

@pytest.mark.asyncio
async def test_drain_all(buffer):
    for i in range(3):
        await buffer.put(bytes([i]))
        
    all_chunks = await buffer.drain_all()
    assert len(all_chunks) == 3
    assert all_chunks == [bytes([0]), bytes([1]), bytes([2])]
    assert buffer.is_empty

@pytest.mark.asyncio
async def test_clear_buffer(buffer):
    for i in range(4):
        await buffer.put(bytes([i]))
    
    buffer.clear()
    assert buffer.is_empty
    assert buffer.chunk_count == 0

@pytest.mark.asyncio
async def test_threadsafe_push(buffer):
    # Simulating background sound device callback running in native thread
    loop = asyncio.get_running_loop()
    raw_audio = b"\xaa\xbb" * 512
    
    buffer.put_threadsafe(raw_audio, loop)
    await asyncio.sleep(0.02)
    
    assert buffer.chunk_count == 1
    result = await buffer.get()
    assert result == raw_audio
