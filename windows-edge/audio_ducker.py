#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audio_ducker.py - Windows Background Audio Ducking via pycaw
Windows Edge Module: Automatically attenuates third-party applications
(e.g., Chrome, Spotify, YouTube) to 10% during voice interactions,
and smoothly restores volume to 100% upon completion or dismiss.
"""

import os
import sys
import time
import logging
import threading
from typing import Dict, Optional, Any

logger = logging.getLogger("windows_edge.audio_ducker")

try:
    import comtypes
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    PYCAW_AVAILABLE = True
except Exception:
    # Attempt to locate .venv site-packages if running with system python
    try:
        module_dir = os.path.dirname(os.path.abspath(__file__))
        root_dir = os.path.dirname(module_dir)
        for cand in [
            os.path.join(root_dir, ".venv", "Lib", "site-packages"),
            os.path.join(module_dir, ".venv", "Lib", "site-packages")
        ]:
            if os.path.isdir(cand) and cand not in sys.path:
                sys.path.insert(0, cand)
        import comtypes
        from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
        PYCAW_AVAILABLE = True
    except Exception as e:
        PYCAW_AVAILABLE = False
        logger.warning(f"[AudioDucker] pycaw not available: {e}")



class AudioDucker:
    """
    Manages system background audio ducking via Windows Core Audio API (pycaw).
    Safely attenuates third-party application audio sessions while preserving
    Voice-In's own audio stream clarity.
    """

    def __init__(self, default_duck_volume: float = 0.0):
        self.default_duck_volume = default_duck_volume
        self._lock = threading.RLock()
        self._saved_volumes: Dict[int, float] = {}  # session_hash -> original_vol
        self._is_ducked = False
        self._my_pid = os.getpid()

    @property
    def is_ducked(self) -> bool:
        with self._lock:
            return self._is_ducked

    def _ensure_com(self):
        """Ensure COM subsystem is initialized for the calling thread."""
        if PYCAW_AVAILABLE:
            try:
                comtypes.CoInitialize()
            except Exception:
                pass

    def duck(self, target_volume: Optional[float] = None, asynchronous: bool = True) -> bool:
        """
        Attenuates all third-party application audio sessions to target_volume (default 0.0 / Hard Mute).
        Skips the current process (Voice-In) to keep Gemini response audio loud and clear.
        Executes asynchronously by default to guarantee < 5ms call latency.
        """
        vol = target_volume if target_volume is not None else self.default_duck_volume
        with self._lock:
            self._is_ducked = True

        if not PYCAW_AVAILABLE:
            logger.debug("[AudioDucker] pycaw unavailable, simulated hard-mute duck active.")
            return True

        def _do_duck():
            self._ensure_com()
            try:
                sessions = AudioUtilities.GetAllSessions()
                ducked_count = 0
                for s in sessions:
                    try:
                        if not s.Process or s.ProcessId == self._my_pid:
                            continue

                        v = s._ctl.QueryInterface(ISimpleAudioVolume)
                        s_id = s.ProcessId
                        current_vol = float(v.GetMasterVolume())

                        # Store original volume if not already tracked
                        with self._lock:
                            if s_id not in self._saved_volumes:
                                self._saved_volumes[s_id] = current_vol if current_vol > 0.05 else 1.0

                        v.SetMasterVolume(vol, None)
                        ducked_count += 1
                        logger.debug(f"[AudioDucker] Hard-Muted {s.Process.name()} (PID {s_id}) to {vol*100:.0f}%")
                    except Exception as sess_err:
                        logger.debug(f"[AudioDucker] Session duck error: {sess_err}")

                logger.info(f"[AudioDucker] Hard-Muted {ducked_count} third-party session(s) to {vol*100:.0f}%.")
            except Exception as e:
                logger.warning(f"[AudioDucker] Failed to duck audio sessions: {e}")

        if asynchronous:
            threading.Thread(target=_do_duck, daemon=True, name="AudioDuckWorker").start()
            return True
        else:
            _do_duck()
            return True

    def unduck(self, smooth: bool = True, asynchronous: bool = True) -> bool:
        """
        Smoothly restores third-party audio sessions back to 100% / original volume.
        Executes asynchronously by default to prevent UI or state-machine thread delays.
        """
        with self._lock:
            if not self._is_ducked and not self._saved_volumes:
                return True
            self._is_ducked = False

        if not PYCAW_AVAILABLE:
            with self._lock:
                self._saved_volumes.clear()
            logger.debug("[AudioDucker] pycaw unavailable, simulated unduck complete.")
            return True

        def _do_unduck():
            self._ensure_com()
            try:
                sessions = AudioUtilities.GetAllSessions()
                to_restore = []
                for s in sessions:
                    try:
                        if not s.Process or s.ProcessId == self._my_pid:
                            continue
                        s_id = s.ProcessId
                        with self._lock:
                            orig_vol = self._saved_volumes.get(s_id, 1.0)
                        v = s._ctl.QueryInterface(ISimpleAudioVolume)
                        to_restore.append((s, v, orig_vol))
                    except Exception:
                        pass

                if smooth and to_restore:
                    # Quick 2-step smooth fade: 55% -> 100% over 40ms
                    for step_ratio in (0.55, 1.0):
                        for s, v, orig in to_restore:
                            try:
                                v.SetMasterVolume(min(1.0, orig * step_ratio), None)
                            except Exception:
                                pass
                        time.sleep(0.02)
                else:
                    for s, v, orig in to_restore:
                        try:
                            v.SetMasterVolume(orig, None)
                        except Exception:
                            pass

                with self._lock:
                    self._saved_volumes.clear()
                logger.info(f"[AudioDucker] Restored {len(to_restore)} audio session(s) back to original volume.")
            except Exception as e:
                logger.warning(f"[AudioDucker] Failed to unduck audio sessions: {e}")
                with self._lock:
                    self._saved_volumes.clear()

        if asynchronous:
            threading.Thread(target=_do_unduck, daemon=True, name="AudioUnduckWorker").start()
            return True
        else:
            _do_unduck()
            return True


# Singleton instance for system-wide access
audio_ducker = AudioDucker()
