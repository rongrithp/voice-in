import io
import re
import unicodedata
import logging
import winreg
from typing import Optional, List, Dict

logger = logging.getLogger("WindowsNativeTTS")

# Registry paths for Windows TTS voices
SAPI5_VOICES_REG_PATH = r"SOFTWARE\Microsoft\Speech\Voices\Tokens"
ONECORE_VOICES_REG_PATH = r"SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens"


def clean_local_tts_text(text: str) -> str:
    """Preprocesses text for optimal local TTS pronunciation and removes noisy markup."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFC", text)
    cleaned = re.sub(r"^#+\s*", "", normalized, flags=re.MULTILINE)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"https?://\S+|www\.\S+", "", cleaned)
    cleaned = re.sub(r"\[\s*[^\]]*\s*\]", "", cleaned)
    cleaned = re.sub(r"[*_~`]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def list_installed_windows_voices() -> List[Dict[str, str]]:
    """Discovers all available SAPI5 and Windows OneCore TTS voices from system registry."""
    voices = []
    
    # 1. Discover OneCore voices (Windows 10/11 modern natural voices like Pattara)
    for reg_path, is_onecore in [(ONECORE_VOICES_REG_PATH, True), (SAPI5_VOICES_REG_PATH, False)]:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path)
            count = winreg.QueryInfoKey(key)[0]
            for i in range(count):
                token_name = winreg.EnumKey(key, i)
                full_token_id = rf"HKEY_LOCAL_MACHINE\{reg_path}\{token_name}"
                
                # Query display name
                try:
                    sub_key = winreg.OpenKey(key, token_name)
                    display_name, _ = winreg.QueryValueEx(sub_key, "")
                except Exception:
                    display_name = token_name
                
                is_thai = any(k in token_name.lower() or k in display_name.lower() for k in ["pattara", "niwat", "premwadee", "achara", "th-th", "thai"])
                voices.append({
                    "id": full_token_id,
                    "name": display_name,
                    "is_onecore": is_onecore,
                    "is_thai": is_thai
                })
        except Exception as e:
            logger.debug(f"[WindowsNativeTTS] Could not read registry {reg_path}: {e}")

    return voices


class WindowsNativeTTSEngine:
    """
    100% Offline Windows Native Text-to-Speech Engine.
    Uses Direct SAPI5 & OneCore Token Dispatch with in-memory SpMemoryStream synthesis.
    Outputs high quality WAV bytes with 0ms cloud latency and full offline resilience.
    """

    def __init__(self, voice_name: Optional[str] = None, speed: float = 1.0):
        self.speed = float(speed)
        self._voice_token_id: Optional[str] = None
        self._voice_display_name: str = "System Default"
        self._available_voices = list_installed_windows_voices()

        self._select_best_voice(preferred_name=voice_name)

    def _select_best_voice(self, preferred_name: Optional[str] = None):
        """Finds best matching voice, prioritizing Thai voices (e.g. Pattara / Niwat)."""
        # 1. Check if user preferred specific voice
        if preferred_name:
            for v in self._available_voices:
                if preferred_name.lower() in v["name"].lower() or preferred_name.lower() in v["id"].lower():
                    self._voice_token_id = v["id"]
                    self._voice_display_name = v["name"]
                    logger.info(f"[WindowsNativeTTS] Selected preferred voice: {self._voice_display_name}")
                    return

        # 2. Prefer Thai OneCore / SAPI voice (Pattara is primary on Windows 10/11)
        for v in self._available_voices:
            if v["is_thai"]:
                self._voice_token_id = v["id"]
                self._voice_display_name = v["name"]
                logger.info(f"[WindowsNativeTTS] Auto-selected Thai Native Voice: {self._voice_display_name}")
                return

        # 3. Fallback to first available voice
        if self._available_voices:
            v = self._available_voices[0]
            self._voice_token_id = v["id"]
            self._voice_display_name = v["name"]
            logger.info(f"[WindowsNativeTTS] Fallback voice: {self._voice_display_name}")

    def set_speed(self, speed_multiplier: float):
        """Sets speed multiplier (0.75x to 2.0x)."""
        self.speed = float(speed_multiplier)

    def set_voice(self, voice_name: str):
        """Updates active voice by name or token id."""
        self._select_best_voice(preferred_name=voice_name)

    def get_voice_name(self) -> str:
        return self._voice_display_name

    def _calculate_sapi_rate(self) -> int:
        """Converts float speed multiplier (0.5x - 2.0x) to SAPI Rate integer (-10 to 10)."""
        if self.speed <= 1.0:
            rate = int((self.speed - 1.0) / 0.5 * 5)
        else:
            rate = int((self.speed - 1.0) / 1.0 * 8)
        return max(-10, min(10, rate))

    def synthesize_to_bytes(self, text: str) -> bytes:
        """
        Synthesizes text directly into in-memory WAV audio bytes (100% offline).
        Uses pythoncom.CoInitialize() for thread-safety across background workers.
        """
        cleaned = clean_local_tts_text(text)
        if not cleaned:
            return b""

        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            try:
                sp_voice = win32com.client.Dispatch("SAPI.SpVoice")
                
                # Set Voice Token if selected
                if self._voice_token_id:
                    try:
                        token = win32com.client.Dispatch("SAPI.SpObjectToken")
                        token.SetId(self._voice_token_id)
                        sp_voice.Voice = token
                    except Exception as token_err:
                        logger.warning(f"[WindowsNativeTTS] Could not set token '{self._voice_token_id}': {token_err}")

                # Set Speaking Rate (-10 to 10)
                sp_voice.Rate = self._calculate_sapi_rate()

                # Create in-memory memory stream with exact SAFT22kHz16BitMono (Type 22)
                mem_stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
                audio_format = win32com.client.Dispatch("SAPI.SpAudioFormat")
                audio_format.Type = 22  # SAFT22kHz16BitMono (Type 22 = 22050Hz 16-bit Mono)
                mem_stream.Format = audio_format
                sp_voice.AudioOutputStream = mem_stream

                # Speak synchronously into memory stream (0 = SVSFDefault)
                sp_voice.Speak(cleaned, 0)

                raw_pcm = bytes(mem_stream.GetData())
                if not raw_pcm:
                    logger.warning("[WindowsNativeTTS] SAPI Speak produced empty audio buffer.")
                    return b""

                import wave
                wav_buffer = io.BytesIO()
                with wave.open(wav_buffer, "wb") as wf:
                    wf.setnchannels(1)       # Mono
                    wf.setsampwidth(2)       # 16-bit (2 bytes)
                    wf.setframerate(22050)   # 22.05kHz matching Format Type 22
                    wf.writeframes(raw_pcm)

                wav_data = wav_buffer.getvalue()
                logger.info(f"[WindowsNativeTTS] Synthesized {len(cleaned)} chars -> {len(wav_data)} bytes WAV (Voice: {self._voice_display_name})")
                return wav_data
            finally:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[WindowsNativeTTS Error] Synthesis failed: {e}")
            return b""
