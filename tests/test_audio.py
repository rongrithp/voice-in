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

def test_is_audio_driver_error():
    from src.audio import is_audio_driver_error
    import sounddevice as sd

    assert is_audio_driver_error(sd.PortAudioError("Error code -9999: Unanticipated host error")) is True
    assert is_audio_driver_error(OSError("MME error 6")) is True
    assert is_audio_driver_error(RuntimeError("PyAudio device unavailable")) is True
    assert is_audio_driver_error(ValueError("random value error")) is False

def test_robust_audio_stream_capture_recovery():
    from unittest.mock import patch, MagicMock
    from src.audio import robust_audio_stream_capture

    attempts = 0
    def mock_read(size):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("Error code -9999: Unanticipated host error")
        return (b"\x00\x00" * 480, False)

    mock_stream = MagicMock()
    mock_stream.read.side_effect = mock_read

    call_count = 0
    def is_active():
        nonlocal call_count
        call_count += 1
        return call_count <= 6

    with patch("src.audio.sd.RawInputStream", return_value=mock_stream):
        generator = robust_audio_stream_capture(
            is_active_callback=is_active,
            retry_delay=0.01
        )
        chunks = list(generator)
        assert len(chunks) >= 1
        assert attempts >= 2

def test_live_audio_stream_producer():
    from unittest.mock import patch, MagicMock
    import time
    from src.audio import LiveAudioStreamProducer

    mock_stream = MagicMock()
    mock_stream.read.return_value = (b"\x00\x00" * 480, False)

    with patch("src.audio.sd.RawInputStream", return_value=mock_stream):
        producer = LiveAudioStreamProducer(sample_rate=16000, channels=1, frame_ms=30)
        producer.start()
        time.sleep(0.005)
        producer.stop()

        assert producer.is_active is False
        assert producer.abort_event.is_set() is True


def test_robust_audio_stream_capture_abort_event():
    import threading
    from unittest.mock import patch, MagicMock
    from src.audio import robust_audio_stream_capture

    abort_evt = threading.Event()
    mock_stream = MagicMock()
    mock_stream.read_available = 480
    mock_stream.read.return_value = (b"\x00\x00" * 480, False)

    with patch("src.audio.sd.RawInputStream", return_value=mock_stream):
        gen = robust_audio_stream_capture(
            is_active_callback=lambda: True,
            abort_event=abort_evt
        )
        chunk = next(gen)
        assert len(chunk) == 960

        abort_evt.set()
        chunks = list(gen)
        assert len(chunks) == 0
        mock_stream.close.assert_called()



def test_wind_harmonics_filter_attenuation_and_vocal_preservation():
    from src.audio import WindHarmonicsFilter, filter_wind_harmonics

    flt = WindHarmonicsFilter(cutoff_hz=80.0, sample_rate=16000)

    # 1. 25Hz low-frequency rumble (wind noise / breath plosive)
    duration_s = 0.2
    t = np.linspace(0, duration_s, int(16000 * duration_s), endpoint=False)
    wind_wave = (np.sin(2 * np.pi * 25.0 * t) * 15000).astype(np.int16)

    filtered_wind = flt.process_samples(wind_wave)
    # Wind rumble energy should be attenuated significantly (> 75% reduction)
    wind_orig_rms = np.sqrt(np.mean(wind_wave.astype(float) ** 2))
    wind_filt_rms = np.sqrt(np.mean(filtered_wind.astype(float) ** 2))
    assert wind_filt_rms < (wind_orig_rms * 0.25)

    # 2. 500Hz natural human voice harmonic
    flt.reset()
    voice_wave = (np.sin(2 * np.pi * 500.0 * t) * 15000).astype(np.int16)
    filtered_voice = flt.process_samples(voice_wave)
    voice_orig_rms = np.sqrt(np.mean(voice_wave.astype(float) ** 2))
    voice_filt_rms = np.sqrt(np.mean(filtered_voice.astype(float) ** 2))
    # Voice band should pass through virtually unattenuated (> 95% pass)
    assert voice_filt_rms >= (voice_orig_rms * 0.95)

    # 3. Convenience function test with bytes
    flt.reset()
    raw_pcm = wind_wave.tobytes()
    filtered_bytes = filter_wind_harmonics(raw_pcm, cutoff_hz=80.0, sample_rate=16000)
    assert isinstance(filtered_bytes, bytes)
    assert len(filtered_bytes) == len(raw_pcm)

    # 4. Zero-UI media filter test
    from src.zero_ui.media import WindHarmonicsFilter as ZeroWindFilter
    zero_flt = ZeroWindFilter(cutoff_hz=80.0, sample_rate=16000)
    zero_clean = zero_flt.process_pcm_bytes(raw_pcm)
    assert len(zero_clean) == len(raw_pcm)
