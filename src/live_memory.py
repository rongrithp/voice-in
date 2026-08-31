import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("LiveMemory")

DEFAULT_MEMORY_FILE = os.path.join(".cache", "live_memory.json")
MAX_STORED_SESSIONS = 10


class LiveSessionMemory:
    """
    Lightweight Rolling Semantic Memory for Gemini Multimodal Live Co-pilot.
    Stores and retrieves persistent condensed context of recent live co-pilot sessions.
    """

    def __init__(self, memory_file: Optional[str] = None, max_stored_sessions: int = MAX_STORED_SESSIONS):
        self.memory_file = memory_file or DEFAULT_MEMORY_FILE
        self.max_stored_sessions = max_stored_sessions
        self._lock = threading.Lock()
        self._ensure_cache_dir()

    def _ensure_cache_dir(self):
        """Ensures the directory for the memory cache file exists."""
        cache_dir = os.path.dirname(self.memory_file)
        if cache_dir and not os.path.exists(cache_dir):
            try:
                os.makedirs(cache_dir, exist_ok=True)
            except Exception as e:
                logger.debug(f"[LiveMemory] Failed creating cache dir {cache_dir}: {e}")

    def load_sessions(self) -> List[Dict[str, Any]]:
        """Loads list of stored session objects from local JSON storage."""
        with self._lock:
            if not os.path.exists(self.memory_file):
                return []
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict) and "sessions" in data:
                        return data["sessions"]
                    return []
            except Exception as e:
                logger.warning(f"[LiveMemory] Error loading memory from {self.memory_file}: {e}")
                return []

    def _save_sessions_to_disk(self, sessions: List[Dict[str, Any]]) -> bool:
        """Writes session list atomically to disk."""
        self._ensure_cache_dir()
        temp_file = f"{self.memory_file}.tmp_{os.getpid()}_{int(time.time()*1000)}"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(sessions, f, ensure_ascii=False, indent=2)
            os.replace(temp_file, self.memory_file)
            return True
        except Exception as e:
            logger.error(f"[LiveMemory] Failed saving memory to {self.memory_file}: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            return False

    def save_session_snapshot(
        self,
        transcript_turns: Union[List[Union[Dict[str, Any], str]], str],
        summary: Optional[str] = None
    ) -> bool:
        """
        Saves a session snapshot with timestamp, transcript dialogue, and a concise summary.
        Maintains rolling window of at most `max_stored_sessions`.
        """
        if not transcript_turns:
            return False

        # Format transcript lines
        formatted_lines: List[str] = []
        if isinstance(transcript_turns, str):
            text = transcript_turns.strip()
            if text:
                formatted_lines = [text]
        elif isinstance(transcript_turns, list):
            for item in transcript_turns:
                if isinstance(item, dict):
                    role = item.get("role", "assistant")
                    text = item.get("text", "").strip()
                    if text:
                        prefix = "User" if role.lower() in ("user", "human") else "Co-pilot"
                        formatted_lines.append(f"{prefix}: {text}")
                elif isinstance(item, str) and item.strip():
                    formatted_lines.append(item.strip())

        if not formatted_lines:
            return False

        # Generate summary if not provided: top 3-5 salient points / dialogue excerpt
        if not summary:
            summary = "\n".join(formatted_lines[-5:])

        session_entry: Dict[str, Any] = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "summary": summary,
            "turns": formatted_lines[-10:]  # keep up to last 10 turns
        }

        with self._lock:
            # Re-read fresh sessions from disk under lock
            sessions = []
            if os.path.exists(self.memory_file):
                try:
                    with open(self.memory_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            sessions = data
                except Exception:
                    sessions = []

            sessions.append(session_entry)
            if len(sessions) > self.max_stored_sessions:
                sessions = sessions[-self.max_stored_sessions:]
            return self._save_sessions_to_disk(sessions)

    def get_rolling_context(self, max_sessions: int = 2) -> str:
        """
        Extracts formatted rolling context from the latest 1-2 sessions for System Instruction injection.
        """
        sessions = self.load_sessions()
        if not sessions:
            return ""

        recent_sessions = sessions[-max_sessions:]
        context_parts: List[str] = []

        for idx, sess in enumerate(recent_sessions, 1):
            ts = sess.get("timestamp", "Recent")
            summary = sess.get("summary", "")
            turns = sess.get("turns", [])

            part = f"- [Session {idx} ({ts})]:\n"
            if summary:
                part += f"  Summary: {summary}\n"
            if turns:
                turns_preview = " | ".join(turns[-3:])
                part += f"  Recent Exchange: {turns_preview}\n"
            context_parts.append(part.strip())

        return "\n".join(context_parts)

    def clear_memory(self) -> bool:
        """Clears all stored session memories."""
        with self._lock:
            if os.path.exists(self.memory_file):
                try:
                    os.remove(self.memory_file)
                    return True
                except Exception as e:
                    logger.warning(f"[LiveMemory] Failed clearing memory: {e}")
                    return False
            return True
