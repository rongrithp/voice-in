import collections
import numpy as np
import webrtcvad
import config

class WebRTCVADSegmenter:
    """
    Real-time continuous audio segmenter using WebRTC VAD.
    Slices 16kHz 16-bit Mono audio stream into spoken phrases, cutting off after
    a specified silence duration (e.g. 350ms).
    """

    def __init__(self, sample_rate: int = config.SAMPLE_RATE, frame_duration_ms: int = config.FRAME_DURATION_MS, silence_cutoff_ms: int = config.VAD_SILENCE_MS, mode: int = config.VAD_MODE, min_speech_duration_ms: int = config.MIN_SPEECH_DURATION_MS):
        self.sample_rate = sample_rate
        self.frame_duration_ms = frame_duration_ms
        self.silence_cutoff_ms = silence_cutoff_ms
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_speech_samples = int(sample_rate * (min_speech_duration_ms / 1000.0))
        self.vad = webrtcvad.Vad(mode)

        # 16-bit mono PCM: 2 bytes per sample
        self.frame_size = int(sample_rate * (frame_duration_ms / 1000.0))
        self.frame_bytes_len = self.frame_size * 2

        self.bytes_buffer = bytearray()
        self.voiced_frames = []
        self.triggered = False
        self.silent_duration_ms = 0
        self.padding_ms = 300
        self.num_padding_frames = self.padding_ms // frame_duration_ms
        self.ring_buffer = collections.deque(maxlen=self.num_padding_frames)

    def reset(self):
        """Reset segmenter internal state."""
        self.bytes_buffer.clear()
        self.voiced_frames.clear()
        self.triggered = False
        self.silent_duration_ms = 0
        self.ring_buffer.clear()

    def process_pcm_chunk(self, raw_pcm_data: bytes):
        """
        Processes incoming raw int16 PCM byte stream and yields completed audio segments (np.ndarray float32)
        when speech end (silence >= silence_cutoff_ms) is detected.
        """
        self.bytes_buffer.extend(raw_pcm_data)

        completed_segments = []

        while len(self.bytes_buffer) >= self.frame_bytes_len:
            frame_bytes = bytes(self.bytes_buffer[:self.frame_bytes_len])
            del self.bytes_buffer[:self.frame_bytes_len]

            is_speech = self.vad.is_speech(frame_bytes, self.sample_rate)

            if not self.triggered:
                self.ring_buffer.append((frame_bytes, is_speech))
                num_voiced = sum(1 for _, speech in self.ring_buffer if speech)

                # Trigger speech segment if > 60% of ring buffer frames are speech
                if num_voiced > 0.6 * self.ring_buffer.maxlen:
                    self.triggered = True
                    for f, _ in self.ring_buffer:
                        self.voiced_frames.append(f)
                    self.ring_buffer.clear()
                    self.silent_duration_ms = 0
            else:
                self.voiced_frames.append(frame_bytes)

                if is_speech:
                    self.silent_duration_ms = 0
                else:
                    self.silent_duration_ms += self.frame_duration_ms

                if self.silent_duration_ms >= self.silence_cutoff_ms:
                    if self.voiced_frames:
                        pcm_all = b"".join(self.voiced_frames)
                        audio_int16 = np.frombuffer(pcm_all, dtype=np.int16)
                        # Net speech samples excluding trailing silence cutoff
                        silence_samples = int(self.sample_rate * (self.silence_cutoff_ms / 1000.0))
                        speech_samples = len(audio_int16) - silence_samples
                        if speech_samples >= self.min_speech_samples:
                            # Normalize to float32 [-1.0, 1.0] for faster-whisper
                            audio_float32 = audio_int16.astype(np.float32) / 32768.0
                            completed_segments.append(audio_float32)
                        else:
                            speech_sec = max(0.0, speech_samples / self.sample_rate)
                            print(f"[VAD Segmenter] Discarded short noise/speech segment ({speech_sec:.2f}s < {self.min_speech_duration_ms}ms)", flush=True)

                    self.triggered = False
                    self.voiced_frames.clear()
                    self.ring_buffer.clear()
                    self.silent_duration_ms = 0

        return completed_segments

    def flush(self):
        """Flush remaining buffered voiced frames as a segment."""
        if self.voiced_frames:
            pcm_all = b"".join(self.voiced_frames)
            audio_int16 = np.frombuffer(pcm_all, dtype=np.int16)
            self.reset()
            if len(audio_int16) >= self.min_speech_samples:
                return audio_int16.astype(np.float32) / 32768.0
        self.reset()
        return None

