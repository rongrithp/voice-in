import io
import re
import unicodedata
import logging
from typing import Optional
import config

logger = logging.getLogger("TTSEngine")

# In-Memory Cache for fast repeated sentence playback (< 1ms)
_tts_cache: dict[str, bytes] = {}
_MAX_CACHE_ENTRIES = 200

def clean_tts_text(text: str) -> str:
    """Preprocesses text for optimal TTS pronunciation and removes noisy markup."""
    if not text:
        return ""

    # Normalize Unicode NFC
    normalized = unicodedata.normalize("NFC", text)

    # Remove Markdown headers (#, ##, etc.)
    cleaned = re.sub(r"^#+\s*", "", normalized, flags=re.MULTILINE)

    # Remove Markdown links [label](url) -> label
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)

    # Remove URLs
    cleaned = re.sub(r"https?://\S+|www\.\S+", "", cleaned)

    # Remove bracketed/parenthetical noise or code annotations
    cleaned = re.sub(r"\[\s*[^\]]*\s*\]", "", cleaned)

    # Replace markdown bold / italics asterisks
    cleaned = re.sub(r"[*_~`]", " ", cleaned)

    # Collapse multiple whitespace / newlines to single space
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned


def split_text_chunks(
    text: str,
    first_chunk_max_len: int = 40,
    normal_chunk_max_len: int = 140,
    max_chunk_len: Optional[int] = None
) -> list[str]:
    """
    Splits long text into progressive natural sentence / clause chunks:
    - Chunk 1 (Micro-Chunk): 25-40 chars for instant Time-to-First-Audio (< 300ms).
    - Chunk 2 onwards: 100-140 chars for smooth background pre-fetching queue.
    """
    if max_chunk_len is not None:
        first_chunk_max_len = min(first_chunk_max_len, max_chunk_len)
        normal_chunk_max_len = max_chunk_len

    cleaned = clean_tts_text(text)
    if not cleaned:
        return []

    # Split by natural sentence delimiters: newlines, punctuation, and spaces
    delimiters = re.split(r'([\n\.?!;,\s]+)', cleaned)
    
    chunks = []
    current_chunk = ""
    target_max_len = first_chunk_max_len

    for token in delimiters:
        if not token:
            continue
        if len(current_chunk) + len(token) <= target_max_len:
            current_chunk += token
        else:
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
                target_max_len = normal_chunk_max_len  # Switch to normal size for remaining chunks
            current_chunk = token

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks if chunks else [cleaned]


class TTSEngine:
    """
    Ultra-Low Latency Google Cloud Text-to-Speech Engine.
    Uses Google Cloud Neural2 and Standard Thai studio voices.
    Features in-memory LRU caching and sub-300ms Time-To-First-Audio.
    """

    def __init__(
        self,
        voice_name: Optional[str] = None,
        language_code: Optional[str] = None,
        speaking_rate: Optional[float] = None
    ):
        self.voice_name = voice_name or getattr(config, "TTS_VOICE", "th-TH-Neural2-C")
        self.language_code = language_code or getattr(config, "TTS_LANGUAGE_CODE", "th-TH")
        self.speaking_rate = float(speaking_rate or getattr(config, "TTS_SPEAKING_RATE", 1.0))
        self._gcp_engine = None

    @property
    def gcp_engine(self):
        if self._gcp_engine is None:
            from src.gcp_tts_engine import GCPTTSEngine
            self._gcp_engine = GCPTTSEngine(
                voice_name=self.voice_name,
                language_code=self.language_code,
                speaking_rate=self.speaking_rate
            )
        return self._gcp_engine

    def clear_cache(self):
        """Clears all in-memory synthesized audio cache."""
        global _tts_cache
        _tts_cache.clear()
        logger.info("[TTSEngine] In-memory audio cache cleared.")

    def set_speed(self, speed_multiplier: float):
        """
        Updates speaking speed multiplier dynamically,
        and flushes in-memory audio cache so new speed applies immediately.
        """
        self.speaking_rate = float(speed_multiplier)
        if self._gcp_engine:
            self._gcp_engine.set_speed(speed_multiplier)
        self.clear_cache()
        logger.info(f"[TTSEngine] Speaking speed updated to {self.speaking_rate:.2f}x (Cache cleared).")

    def set_voice(self, voice_name: str):
        """
        Updates active voice and flushes in-memory cache so next synthesis uses new voice.
        """
        self.voice_name = voice_name
        if self._gcp_engine:
            self._gcp_engine.set_voice(voice_name)
        self.clear_cache()
        logger.info(f"[TTSEngine] Voice updated to '{voice_name}' (Cache cleared).")

    def warmup(self):
        """Pre-warms Google Cloud TTS client connection in RAM."""
        try:
            self.gcp_engine.warmup()
        except Exception as e:
            logger.warning(f"[TTSEngine Warmup Notice] GCP TTS: {e}")

    def synthesize(self, text: str) -> bytes:
        """
        Synthesizes text to MP3 audio bytes using Google Cloud TTS with LRU caching.
        """
        cleaned = clean_tts_text(text)
        if not cleaned:
            logger.warning("[TTSEngine] Empty text provided for synthesis.")
            return b""

        # 1. In-Memory Cache Lookup (< 1ms)
        cache_key = f"{self.voice_name}:{self.speaking_rate}:{cleaned}"
        if cache_key in _tts_cache:
            logger.info(f"[TTSEngine Cache Hit] Returning {len(_tts_cache[cache_key])}B cached audio for '{cleaned[:30]}...'")
            return _tts_cache[cache_key]

        # 2. Synthesize via Google Cloud TTS
        try:
            audio_bytes = self.gcp_engine.synthesize(cleaned)
            if audio_bytes:
                self._save_cache(cache_key, audio_bytes)
                return audio_bytes
        except Exception as e:
            logger.error(f"[TTSEngine Error] Google Cloud TTS synthesis failed: {e}")

        return b""

    def _save_cache(self, key: str, audio_bytes: bytes):
        if len(_tts_cache) >= _MAX_CACHE_ENTRIES:
            _tts_cache.pop(next(iter(_tts_cache)))
        _tts_cache[key] = audio_bytes
