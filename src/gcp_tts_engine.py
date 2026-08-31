import io
import os
import logging
from typing import Optional
import config

logger = logging.getLogger("GCPTTSEngine")

class GCPTTSEngine:
    """
    Ultra-Low Latency Google Cloud Text-to-Speech Engine.
    Uses Google Neural2 / Standard Thai & English neural voices via gRPC/REST.
    Yields MP3 audio streams in < 250ms for instant Time-To-First-Audio.
    """

    def __init__(
        self,
        credentials_path: Optional[str] = None,
        voice_name: Optional[str] = None,
        language_code: Optional[str] = None,
        speaking_rate: Optional[float] = None
    ):
        self.credentials_path = credentials_path or getattr(config, "GOOGLE_APPLICATION_CREDENTIALS", "service_account.json")
        self.voice_name = voice_name or getattr(config, "GCP_TTS_VOICE", "th-TH-Neural2-C")
        self.language_code = language_code or getattr(config, "TTS_LANGUAGE_CODE", "th-TH")
        self.speaking_rate = float(speaking_rate or getattr(config, "TTS_SPEAKING_RATE", 1.0))
        self._client = None

    @property
    def client(self):
        """Lazy initialized Google Cloud TextToSpeechClient."""
        if self._client is None:
            from google.cloud import texttospeech
            from google.oauth2 import service_account

            if os.path.exists(self.credentials_path):
                creds = service_account.Credentials.from_service_account_file(self.credentials_path)
                self._client = texttospeech.TextToSpeechClient(credentials=creds)
            else:
                self._client = texttospeech.TextToSpeechClient()
        return self._client

    def warmup(self) -> None:
        """Pure in-memory warmup - zero network delay."""
        logger.info("[GCPTTSEngine] Engine ready (In-memory Lazy Connect).")

    def set_speed(self, speed_multiplier: float):
        """Updates speaking rate dynamically."""
        self.speaking_rate = float(speed_multiplier)
        logger.info(f"[GCPTTSEngine] Speaking rate set to {self.speaking_rate:.2f}x")

    def set_voice(self, voice_name: str):
        """Updates GCP voice name dynamically."""
        self.voice_name = voice_name
        logger.info(f"[GCPTTSEngine] Voice updated to '{self.voice_name}'")

    def synthesize(self, text: str) -> bytes:
        """
        Synthesizes text to MP3 audio bytes using Google Cloud TTS.
        """
        if not text or not text.strip():
            return b""

        try:
            from google.cloud import texttospeech

            synthesis_input = texttospeech.SynthesisInput(text=text.strip())
            voice = texttospeech.VoiceSelectionParams(
                language_code=self.language_code,
                name=self.voice_name
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3,
                speaking_rate=self.speaking_rate
            )

            response = self.client.synthesize_speech(
                input=synthesis_input,
                voice=voice,
                audio_config=audio_config
            )
            audio_bytes = response.audio_content
            if audio_bytes:
                logger.info(f"[GCPTTSEngine] Synthesized {len(audio_bytes)}B audio for '{text[:25]}...'")
                return audio_bytes
        except Exception as e:
            logger.error(f"[GCPTTSEngine Error] Google Cloud TTS synthesis failed: {e}")
            raise e

        return b""
