"""
Edge Media Processing, Audio Control & Watchdogs.
Includes:
- Edge Image Compressor (RAM-based resize to <=1280px & JPEG/WebP compression)
- Client-Side Time-Stretching Audio Sink (WSOLA/OLA 0.75x-1.5x pitch-preserving scaling)
- Client Inactivity RMS Noise Gate & VAD Watchdogs
"""

from __future__ import annotations
import io
import math
import struct
import time
import logging
from typing import Optional, Callable, List

logger = logging.getLogger("zero_ui.media")


# --- 1. Edge Image Compressor ---

def compress_image_frame(
    image_bytes: bytes,
    max_dim: int = 1280,
    quality: int = 85,
    output_format: str = "JPEG"
) -> bytes:
    """
    Resizes image frame in RAM to max edge <= max_dim (1024-1280px) and compresses to JPEG/WebP.
    """
    if not image_bytes:
        return b""

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))

        # Convert RGBA to RGB for JPEG compatibility
        if img.mode in ("RGBA", "P") and output_format.upper() in ("JPEG", "JPG"):
            img = img.convert("RGB")

        w, h = img.size
        if max(w, h) > max_dim:
            scale = max_dim / float(max(w, h))
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        out_buf = io.BytesIO()
        img.save(out_buf, format=output_format.upper(), quality=quality, optimize=True)
        return out_buf.getvalue()
    except Exception as e:
        logger.warning(f"Image compression failed, falling back to original: {e}")
        return image_bytes


# --- 2. Client-Side Time-Stretching Audio Sink ---

def time_stretch_pcm(
    pcm_bytes: bytes,
    speed: float = 1.0,
    sample_rate: int = 24000,
    sample_width: int = 2
) -> bytes:
    """
    Time-stretches 16-bit mono PCM audio (0.75x to 1.5x) using Overlap-Add (OLA/WSOLA)
    preserving the original acoustic pitch without cloud re-negotiation.
    """
    if speed == 1.0 or not pcm_bytes or len(pcm_bytes) < sample_width:
        return pcm_bytes

    # Clamp speed to supported range [0.75, 1.5]
    speed = max(0.75, min(1.5, speed))

    num_samples = len(pcm_bytes) // sample_width
    if num_samples < 200:
        return pcm_bytes

    samples = struct.unpack(f"<{num_samples}h", pcm_bytes[:num_samples * sample_width])

    window_size = int(sample_rate * 0.025)
    if window_size % 2 != 0:
        window_size += 1

    hop_s = window_size // 2  # synthesis hop
    hop_a = max(1, int(hop_s * speed))  # analysis hop

    # Hann window
    window = [0.5 * (1.0 - math.cos(2.0 * math.pi * i / (window_size - 1))) for i in range(window_size)]

    target_out_len = int(num_samples / speed)
    output = [0.0] * (target_out_len + window_size * 2)
    weights = [0.0] * (target_out_len + window_size * 2)

    in_pos = 0
    out_pos = 0

    while in_pos + window_size <= num_samples and out_pos + window_size <= len(output):
        for i in range(window_size):
            output[out_pos + i] += samples[in_pos + i] * window[i]
            weights[out_pos + i] += window[i]

        in_pos += hop_a
        out_pos += hop_s

    # Normalize overlapping regions
    final_len = min(target_out_len, out_pos)
    final_samples = []
    for i in range(final_len):
        if weights[i] > 1e-4:
            val = output[i] / weights[i]
        else:
            val = output[i]
        final_samples.append(max(-32768, min(32767, int(val))))

    return struct.pack(f"<{len(final_samples)}h", *final_samples)


class TimeStretchAudioSink:
    """
    Sink for incoming 24kHz/16kHz PCM audio chunks with dynamic client playback speed control
    and user-space Play/Pause/Halt toggling.
    """

    def __init__(self, sample_rate: int = 24000, playback_speed: float = 1.0):
        self.sample_rate = sample_rate
        self.playback_speed = max(0.75, min(1.5, playback_speed))
        self.is_paused: bool = False

    def set_speed(self, speed: float) -> None:
        self.playback_speed = max(0.75, min(1.5, speed))

    def toggle_playback(self) -> bool:
        """
        Toggles play/pause state.
        Returns True if playback is active/resumed, False if paused/halted.
        """
        self.is_paused = not self.is_paused
        return not self.is_paused

    def halt(self) -> None:
        """Halts active audio playback."""
        self.is_paused = True

    def resume(self) -> None:
        """Resumes audio playback."""
        self.is_paused = False

    def process_chunk(self, chunk_pcm_bytes: bytes) -> bytes:
        if self.is_paused:
            return b""
        if self.playback_speed == 1.0:
            return chunk_pcm_bytes
        return time_stretch_pcm(
            chunk_pcm_bytes,
            speed=self.playback_speed,
            sample_rate=self.sample_rate
        )


# --- 2.5 Real-Time Wind Harmonics High-Pass Filter ---

class WindHarmonicsFilter:
    """
    Real-time 2nd-order IIR Butterworth High-Pass Filter.
    Cuts low-frequency wind rumble, breathing plosives, and mechanical HVAC vibrations (< 80Hz)
    while preserving natural human speech clarity (> 100-3000Hz).
    Operates statelessly across streaming int16 PCM chunks with minimal overhead.
    """

    def __init__(self, cutoff_hz: float = 80.0, sample_rate: int = 16000):
        self.cutoff_hz = float(cutoff_hz)
        self.sample_rate = int(sample_rate)
        self._update_coefficients()
        self.reset()

    def _update_coefficients(self) -> None:
        w0 = 2.0 * math.pi * self.cutoff_hz / self.sample_rate
        alpha = math.sin(w0) / (2.0 * 0.7071067811865475)  # Q = 1/sqrt(2)
        cos_w0 = math.cos(w0)

        b0 = (1.0 + cos_w0) / 2.0
        b1 = -(1.0 + cos_w0)
        b2 = (1.0 + cos_w0) / 2.0
        a0 = 1.0 + alpha
        a1 = -2.0 * cos_w0
        a2 = 1.0 - alpha

        self.b0 = float(b0 / a0)
        self.b1 = float(b1 / a0)
        self.b2 = float(b2 / a0)
        self.a1 = float(a1 / a0)
        self.a2 = float(a2 / a0)

    def reset(self) -> None:
        self.x1 = 0.0
        self.x2 = 0.0
        self.y1 = 0.0
        self.y2 = 0.0

    def process_pcm_bytes(self, pcm_bytes: bytes, sample_width: int = 2) -> bytes:
        if not pcm_bytes or len(pcm_bytes) < sample_width:
            return pcm_bytes

        num_samples = len(pcm_bytes) // sample_width
        samples = struct.unpack(f"<{num_samples}h", pcm_bytes[:num_samples * sample_width])

        b0, b1, b2 = self.b0, self.b1, self.b2
        a1, a2 = self.a1, self.a2
        x1, x2 = self.x1, self.x2
        y1, y2 = self.y1, self.y2

        out_samples = []
        for x in samples:
            y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2 = x1
            x1 = x
            y2 = y1
            y1 = y
            val = max(-32768, min(32767, int(y)))
            out_samples.append(val)

        self.x1, self.x2 = x1, x2
        self.y1, self.y2 = y1, y2
        return struct.pack(f"<{len(out_samples)}h", *out_samples)


def filter_wind_harmonics(pcm_bytes: bytes, cutoff_hz: float = 80.0, sample_rate: int = 16000) -> bytes:
    """Convenience helper to filter wind rumble from raw int16 PCM bytes."""
    flt = WindHarmonicsFilter(cutoff_hz=cutoff_hz, sample_rate=sample_rate)
    return flt.process_pcm_bytes(pcm_bytes)


# --- 3. RMS Noise Gate & VAD Watchdogs ---

def calculate_pcm_rms(pcm_bytes: bytes, sample_width: int = 2) -> float:
    """
    Calculates normalized Root Mean Square (RMS) energy (0.0 to 1.0) of 16-bit mono PCM audio.
    """
    num_samples = len(pcm_bytes) // sample_width
    if num_samples == 0:
        return 0.0

    samples = struct.unpack(f"<{num_samples}h", pcm_bytes[:num_samples * sample_width])
    sum_squares = sum((s / 32768.0) ** 2 for s in samples)
    return math.sqrt(sum_squares / num_samples)


class RMSNoiseGate:
    """
    Suppresses audio streaming when background acoustic energy is below threshold.
    """

    def __init__(self, threshold: float = 0.015):
        self.threshold = threshold

    def is_speech_active(self, pcm_bytes: bytes) -> bool:
        return calculate_pcm_rms(pcm_bytes) >= self.threshold


class DynamicRMSNoiseGate:
    """
    Dynamic background noise floor tracking and silence teardown detection.
    Continuously updates noise floor baseline from ambient background energy.
    If speech energy drops to the noise floor for over silence_teardown_sec (default 5.0s),
    invokes on_silence_teardown callback to send EndStreamFrame, flush buffers,
    and drop client FSM back to STANDBY_DORMANT.
    """

    def __init__(
        self,
        initial_floor: float = 0.010,
        silence_teardown_sec: float = 5.0,
        adaptation_rate: float = 0.05,
        on_silence_teardown: Optional[Callable[[], None]] = None
    ):
        self.noise_floor = initial_floor
        self.silence_teardown_sec = silence_teardown_sec
        self.adaptation_rate = adaptation_rate
        self.on_silence_teardown = on_silence_teardown
        self.silence_start_time: Optional[float] = None
        self.is_streaming: bool = False

    def update_floor(self, ambient_rms: float) -> None:
        """Adapts running background noise floor."""
        self.noise_floor = (1.0 - self.adaptation_rate) * self.noise_floor + self.adaptation_rate * ambient_rms

    def process_pcm_frame(self, pcm_bytes: bytes, now: Optional[float] = None) -> bool:
        """
        Processes incoming PCM frame.
        Returns True if speech active, False if at or below noise floor.
        If at/below noise floor for > silence_teardown_sec, invokes on_silence_teardown.
        """
        t = now if now is not None else time.time()
        rms = calculate_pcm_rms(pcm_bytes)
        threshold = max(0.012, self.noise_floor * 1.5)

        if rms >= threshold:
            self.silence_start_time = None
            self.is_streaming = True
            return True
        else:
            self.update_floor(rms)
            if self.silence_start_time is None:
                self.silence_start_time = t
            elif (t - self.silence_start_time) >= self.silence_teardown_sec:
                if self.on_silence_teardown and self.is_streaming:
                    self.is_streaming = False
                    self.on_silence_teardown()
            return False


class ClientInactivityWatchdog:
    """
    Monitors speech silence intervals (Tier 1) and triggers standby transitions (Tier 3).
    """

    def __init__(
        self,
        vad_silence_timeout_sec: float = 3.0,
        dormant_timeout_sec: float = 60.0,
        on_silence_timeout: Optional[Callable[[], None]] = None,
        on_dormant_timeout: Optional[Callable[[], None]] = None
    ):
        self.vad_silence_timeout_sec = vad_silence_timeout_sec
        self.dormant_timeout_sec = dormant_timeout_sec
        self.on_silence_timeout = on_silence_timeout
        self.on_dormant_timeout = on_dormant_timeout

        self.last_speech_time = time.time()
        self.last_activity_time = time.time()
        self.is_silence_paused = False
        self.is_dormant = False

    def report_activity(self, is_speech: bool = True) -> None:
        now = time.time()
        self.last_activity_time = now
        if is_speech:
            self.last_speech_time = now
            self.is_silence_paused = False
        self.is_dormant = False

    def check_timers(self) -> None:
        now = time.time()

        # Check VAD silence timeout (Tier 1)
        if not self.is_silence_paused and (now - self.last_speech_time) >= self.vad_silence_timeout_sec:
            self.is_silence_paused = True
            if self.on_silence_timeout:
                self.on_silence_timeout()

        # Check Deep Dormant timeout (Tier 3)
        if not self.is_dormant and (now - self.last_activity_time) >= self.dormant_timeout_sec:
            self.is_dormant = True
            if self.on_dormant_timeout:
                self.on_dormant_timeout()
