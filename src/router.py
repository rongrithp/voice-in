import io
import os
import json
import wave
import base64
import logging
import httpx
import numpy as np
import config

logger = logging.getLogger("TranscribeEngine")

SYSTEM_INSTRUCTION = "You are an automated Speech-to-Text engine for Thai and English. Your ONLY task is verbatim transcription of spoken words. NEVER reply to questions, NEVER assist, NEVER converse, and NEVER explain. Output ONLY the exact transcribed speech. If the audio is silent or unclear, output ABSOLUTELY NOTHING."

DEFAULT_TRANSCRIBE_PROMPT = "Transcribe the spoken audio verbatim in Thai or English. Preserve all syllables and do not truncate words. If no speech is present, return empty string."

PROMPT_LEAK_PATTERNS = [
    "you are an automated",
    "you are an accurate",
    "speech-to-text",
    "transcribe the following",
    "transcribe the spoken",
    "output only the verbatim",
    "output absolutely nothing",
    "verbatim transcription",
    "thai and english",
    "exact spoken words",
    "if the audio is silent",
    "if no speech is present",
    "empty string",
    "i'm not sure",
    "i am not sure",
    "i cannot",
    "i can't",
    "i am unable",
    "i'm sorry",
    "i am sorry",
    "as an ai",
    "as a language model",
    "how can i help",
    "how can i assist",
    "what can i do for you",
]

def is_prompt_leak_or_hallucination(text: str) -> bool:
    if not text:
        return False
    lower = text.lower().strip()
    for pattern in PROMPT_LEAK_PATTERNS:
        if pattern in lower:
            return True
    if lower.startswith(("i'm not sure", "i am not sure", "i cannot", "i can't", "i am unable", "i'm sorry", "i am sorry", "sorry,", "there is no audio", "there is no speech")):
        return True
    return False


def numpy_to_wav_bytes(audio_data: np.ndarray, sample_rate: int = config.SAMPLE_RATE) -> bytes:
    """Converts a 1D float32 or int16 numpy array to standard 16-bit PCM WAV bytes in memory using wave module."""
    if audio_data is None or len(audio_data) == 0:
        return b""
    if audio_data.dtype != np.int16:
        # Assuming float32 in [-1.0, 1.0]
        pcm_int16 = (np.clip(audio_data, -1.0, 1.0) * 32767.0).astype(np.int16)
    else:
        pcm_int16 = audio_data

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(getattr(config, "CHANNELS", 1))
        wf.setsampwidth(2)  # 16-bit (2 bytes per sample)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_int16.tobytes())
    return buf.getvalue()


class GeminiTranscribeEngine:
    """
    Ultra-lightweight Cloud Speech-to-Text transcriber calling Gemini REST API directly via httpx.
    Zero heavy SDK imports, zero discovery overhead, sub-millisecond instantiation.
    """
    def __init__(self, model_name: str = getattr(config, "GEMINI_MODEL", "gemini-2.5-flash")):
        self.model_name = model_name
        self.api_key = getattr(config, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        self._client = None
        logger.info(f"[GeminiTranscribeEngine] Configured direct HTTP/1.1 REST client for model: '{self.model_name}'")

    @property
    def client(self):
        if self._client is None:
            self._client = httpx.Client(
                http2=False,
                timeout=httpx.Timeout(10.0, connect=3.0),
                limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=120.0)
            )
        return self._client

    @client.setter
    def client(self, val):
        self._client = val

    def warmup(self):
        """Pre-initializes the HTTP client in-memory without blocking network requests."""
        try:
            _ = self.client
        except Exception as e:
            logger.debug(f"[Gemini STT Warmup] Client init notice: {e}")

    def _build_payload(self, wav_bytes: bytes) -> dict:
        b64_audio = base64.b64encode(wav_bytes).decode("utf-8")
        return {
            "contents": [
                {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": "audio/wav",
                                "data": b64_audio
                            }
                        },
                        {
                            "text": DEFAULT_TRANSCRIBE_PROMPT
                        }
                    ]
                }
            ],
            "systemInstruction": {
                "parts": [
                    {
                        "text": SYSTEM_INSTRUCTION
                    }
                ]
            },
            "generationConfig": {
                "temperature": 0.0,
                "thinkingConfig": {
                    "thinkingBudget": 0
                }
            }
        }

    def transcribe(self, audio_data: np.ndarray) -> str:
        """Transcribe audio chunk (float32 or int16 numpy array) using Gemini REST API."""
        if audio_data is None or len(audio_data) == 0:
            return ""

        wav_bytes = numpy_to_wav_bytes(audio_data, sample_rate=config.SAMPLE_RATE)
        if not wav_bytes:
            return ""

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent?key={self.api_key}"
        payload = self._build_payload(wav_bytes)

        try:
            response = self.client.post(url, json=payload)
            if response.status_code != 200:
                logger.error(f"[Gemini REST Error] Status {response.status_code}: {response.text}")
                return ""

            data = response.json()
            transcribed_text = ""
            candidates = data.get("candidates", [])
            if candidates:
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                transcribed_text = "".join(p.get("text", "") for p in parts).strip()

            if not transcribed_text:
                logger.info(f"[Gemini STT Raw] Model returned empty text. Response: {data}")
            elif is_prompt_leak_or_hallucination(transcribed_text):
                logger.info(f"[Gemini STT Filter] Dropped prompt-leak / hallucination: '{transcribed_text}'")
                transcribed_text = ""
            else:
                logger.info(f"[Gemini STT Raw] Transcribed: '{transcribed_text}'")

            return transcribed_text
        except Exception as e:
            logger.error(f"[Gemini STT Error] REST call failed: {e}")
            return ""

    def stream_transcribe(self, audio_data: np.ndarray):
        """Transcribe audio chunk using streaming SSE response from Gemini REST API."""
        if audio_data is None or len(audio_data) == 0:
            return

        wav_bytes = numpy_to_wav_bytes(audio_data, sample_rate=config.SAMPLE_RATE)
        if not wav_bytes:
            return

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:streamGenerateContent?alt=sse&key={self.api_key}"
        payload = self._build_payload(wav_bytes)

        try:
            with self.client.stream("POST", url, json=payload) as response:
                if response.status_code != 200:
                    logger.error(f"[Gemini REST Stream Error] Status {response.status_code}")
                    return

                for line in response.iter_lines():
                    if line.startswith("data: "):
                        raw_json = line[6:].strip()
                        if raw_json:
                            try:
                                chunk_data = json.loads(raw_json)
                                candidates = chunk_data.get("candidates", [])
                                if candidates:
                                    content = candidates[0].get("content", {})
                                    parts = content.get("parts", [])
                                    for p in parts:
                                        t = p.get("text", "")
                                        if t and not is_prompt_leak_or_hallucination(t):
                                            yield t
                            except Exception:
                                pass
        except Exception as e:
            logger.error(f"[Gemini STT Stream Error] Stream failed: {e}")


    def create_live_session(self, on_token_callback):
        """Creates and returns a real-time Bidirectional WebSocket live streaming session."""
        from src.live_gemini_engine import GeminiLiveStreamSession
        live_model = getattr(config, "GEMINI_LIVE_MODEL", "gemini-2.0-flash")
        return GeminiLiveStreamSession(
            on_token_callback=on_token_callback,
            api_key=self.api_key,
            model_name=live_model
        )


class TranscribeEngine:
    """
    Primary router for transcription services.
    Supports Google Cloud Speech (gRPC streaming), Gemini 2.5 Flash (Cloud API), and LocalWhisperEngine.
    """
    def __init__(self, engine_type: str = getattr(config, "STT_ENGINE", "gcp")):
        self.engine_type = engine_type.lower()
        if self.engine_type in ("gcp", "google-cloud-speech", "google_speech", "grpc"):
            from src.gcp_speech_engine import GCPSpeechEngine
            self.engine = GCPSpeechEngine(
                credentials_path=getattr(config, "GOOGLE_APPLICATION_CREDENTIALS", "service_account.json"),
                language_code=getattr(config, "LANGUAGE_CODE", "th-TH")
            )
        elif self.engine_type in ("local", "whisper", "faster-whisper"):
            from src.local_engine import LocalWhisperEngine
            self.engine = LocalWhisperEngine()
        else:
            self.engine = GeminiTranscribeEngine(
                model_name=getattr(config, "GEMINI_MODEL", "gemini-2.5-flash")
            )

    def transcribe(self, audio_chunk: np.ndarray) -> str:
        return self.engine.transcribe(audio_chunk)

    def stream_transcribe(self, audio_chunk: np.ndarray):
        return self.engine.stream_transcribe(audio_chunk)

    def create_live_session(self, on_token_callback):
        if hasattr(self.engine, "create_live_session"):
            return self.engine.create_live_session(on_token_callback)
        return None

    def warmup(self):
        if hasattr(self.engine, "warmup"):
            self.engine.warmup()
