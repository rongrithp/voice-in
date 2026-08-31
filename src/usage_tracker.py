import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("UsageTracker")

USD_TO_THB = 36.00
COST_STT_PER_SEC_USD = 0.016 / 60.0              # $0.016 / minute
COST_TTS_NEURAL2_PER_CHAR_USD = 16.0 / 1_000_000 # $16.00 / 1M chars
COST_TTS_STANDARD_PER_CHAR_USD = 4.0 / 1_000_000 # $4.00 / 1M chars

class UsageTracker:
    """
    Monthly Partitioned Usage & Cost Tracker for Google Cloud STT & TTS.
    Maintains persistent usage stats per billing cycle (YYYY-MM) in Thai Baht (THB).
    """

    def __init__(self, storage_path="data/usage_stats.json"):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _current_month_key(self) -> str:
        return datetime.now().strftime("%Y-%m")

    def _load(self) -> dict:
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "months" in data and isinstance(data["months"], dict):
                        return data
                    # Migration from legacy flat schema
                    if "stt_audio_seconds" in data:
                        month_key = self._current_month_key()
                        return {
                            "months": {
                                month_key: {
                                    "stt_audio_seconds": float(data.get("stt_audio_seconds", 0.0)),
                                    "stt_requests": int(data.get("stt_requests", 0)),
                                    "tts_neural2_chars": int(data.get("tts_neural2_chars", 0)),
                                    "tts_standard_chars": int(data.get("tts_standard_chars", 0)),
                                    "tts_requests": int(data.get("tts_requests", 0))
                                }
                            },
                            "last_active_month": month_key
                        }
            except Exception as e:
                logger.warning(f"[UsageTracker] Failed to parse usage_stats.json: {e}")
        return {"months": {}, "last_active_month": self._current_month_key()}

    def _ensure_month_entry(self, month_key: str):
        if month_key not in self.data["months"]:
            self.data["months"][month_key] = {
                "stt_audio_seconds": 0.0,
                "stt_requests": 0,
                "tts_neural2_chars": 0,
                "tts_standard_chars": 0,
                "tts_requests": 0
            }

    def record_stt(self, duration_sec: float):
        if duration_sec <= 0:
            return
        month_key = self._current_month_key()
        self._ensure_month_entry(month_key)
        self.data["months"][month_key]["stt_audio_seconds"] += float(duration_sec)
        self.data["months"][month_key]["stt_requests"] += 1
        self.data["last_active_month"] = month_key
        self._save()

    def record_tts(self, char_count: int, is_neural: bool = True):
        if char_count <= 0:
            return
        month_key = self._current_month_key()
        self._ensure_month_entry(month_key)
        if is_neural:
            self.data["months"][month_key]["tts_neural2_chars"] += int(char_count)
        else:
            self.data["months"][month_key]["tts_standard_chars"] += int(char_count)
        self.data["months"][month_key]["tts_requests"] += 1
        self.data["last_active_month"] = month_key
        self._save()

    def reset_stats(self, month_key: str = None):
        """Resets statistics for the specified or current month."""
        target_month = month_key or self._current_month_key()
        self.data["months"][target_month] = {
            "stt_audio_seconds": 0.0,
            "stt_requests": 0,
            "tts_neural2_chars": 0,
            "tts_standard_chars": 0,
            "tts_requests": 0
        }
        self._save()
        logger.info(f"[UsageTracker] Usage statistics reset for {target_month}.")

    def get_current_month_summary(self) -> dict:
        month_key = self._current_month_key()
        self._ensure_month_entry(month_key)
        m_data = self.data["months"][month_key]

        stt_cost_thb = m_data["stt_audio_seconds"] * COST_STT_PER_SEC_USD * USD_TO_THB
        tts_cost_thb = (
            (m_data["tts_neural2_chars"] * COST_TTS_NEURAL2_PER_CHAR_USD) +
            (m_data["tts_standard_chars"] * COST_TTS_STANDARD_PER_CHAR_USD)
        ) * USD_TO_THB
        total_cost_thb = stt_cost_thb + tts_cost_thb

        return {
            "month": month_key,
            "stt_sec": m_data["stt_audio_seconds"],
            "stt_min": m_data["stt_audio_seconds"] / 60.0,
            "stt_requests": m_data["stt_requests"],
            "tts_chars": m_data["tts_neural2_chars"] + m_data["tts_standard_chars"],
            "tts_requests": m_data["tts_requests"],
            "stt_cost_thb": stt_cost_thb,
            "tts_cost_thb": tts_cost_thb,
            "total_cost_thb": total_cost_thb
        }

    # Backward compatibility alias
    get_cost_summary = get_current_month_summary

    def _save(self):
        try:
            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.error(f"[UsageTracker Error] Failed to save stats: {e}")
