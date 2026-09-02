"""
Process Mutex / Single Instance Guard for Windows and Cross-Platform Environments.
Prevents duplicate processes from conflicting over the same hardware audio endpoint
and eliminates Kernel I/O Deadlocks.
"""

import os
import sys
import tempfile
import logging
from typing import Optional, Any

logger = logging.getLogger("SingleInstance")


class SingleInstanceGuard:
    """
    Process mutex / single instance guard.
    Uses a Win32 Named Mutex on Windows and an OS-level file lock fallback
    to prevent multiple processes from conflicting over hardware audio streams.
    """

    def __init__(self, app_id: str = "VoiceOperatingHub_SingleInstance_Mutex"):
        self.app_id = app_id
        self.mutex: Optional[Any] = None
        self._lock_file: Optional[Any] = None
        self._lock_path: Optional[str] = None
        self.is_locked: bool = False

    def acquire(self) -> bool:
        """
        Attempts to acquire single instance ownership.
        Returns True if acquired successfully, False if another instance is already running.
        """
        if self.is_locked:
            return True

        # 1. Primary Win32 Named Mutex (Windows)
        if sys.platform == "win32":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                ERROR_ALREADY_EXISTS = 183

                mutex_name = f"Global\\{self.app_id}"
                # CreateMutexW: lpMutexAttributes=None, bInitialOwner=False, lpName=mutex_name
                handle = kernel32.CreateMutexW(None, False, mutex_name)
                last_error = kernel32.GetLastError()

                if handle and last_error == ERROR_ALREADY_EXISTS:
                    logger.warning(f"[SingleInstance] Another instance is already active (Win32 Mutex: {mutex_name}).")
                    try:
                        kernel32.CloseHandle(handle)
                    except Exception:
                        pass
                    return False
                elif handle:
                    self.mutex = handle
                    self.is_locked = True
                    return True
            except Exception as e:
                logger.warning(f"[SingleInstance] Win32 mutex creation failed: {e}. Falling back to file lock.")

        # 2. File Lock Fallback (Windows msvcrt / Unix fcntl)
        try:
            self._lock_path = os.path.join(tempfile.gettempdir(), f"{self.app_id}.lock")
            self._lock_file = open(self._lock_path, "w")
            try:
                import msvcrt
                try:
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    self.is_locked = True
                    return True
                except (IOError, OSError):
                    logger.warning(f"[SingleInstance] Lockfile {self._lock_path} is held by another process.")
                    self._lock_file.close()
                    self._lock_file = None
                    return False
            except ImportError:
                try:
                    import fcntl
                    try:
                        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        self.is_locked = True
                        return True
                    except (IOError, OSError):
                        logger.warning(f"[SingleInstance] Lockfile {self._lock_path} is held by another process.")
                        self._lock_file.close()
                        self._lock_file = None
                        return False
                except ImportError:
                    self.is_locked = True
                    return True
        except Exception as e:
            logger.warning(f"[SingleInstance] File lock fallback failed: {e}")
            return True

    def release(self) -> None:
        """Releases the instance mutex and file lock handles."""
        if not self.is_locked and not self.mutex and not self._lock_file:
            return

        if sys.platform == "win32" and self.mutex:
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self.mutex)
            except Exception:
                pass
            self.mutex = None

        if self._lock_file:
            try:
                try:
                    import msvcrt
                    try:
                        msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                except ImportError:
                    try:
                        import fcntl
                        try:
                            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                        except Exception:
                            pass
                    except ImportError:
                        pass
                self._lock_file.close()
            except Exception:
                pass
            self._lock_file = None

        self.is_locked = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def __del__(self):
        self.release()
