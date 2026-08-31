import logging
import threading
from typing import Optional

try:
    import comtypes
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    PYCAW_AVAILABLE = True
except ImportError:
    PYCAW_AVAILABLE = False

logger = logging.getLogger("AudioControl")

class WindowsAudioController:
    """
    Controls Windows Master Audio via Windows Core Audio API (pycaw).
    Handles COM initialization safely across multiple background threads.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._previous_mute_state: Optional[bool] = None

    def _ensure_com(self):
        """Ensure COM is initialized on the current thread."""
        if PYCAW_AVAILABLE:
            try:
                comtypes.CoInitialize()
            except Exception:
                pass

    def _get_endpoint_volume(self):
        """Retrieve the primary speaker IAudioEndpointVolume interface."""
        if not PYCAW_AVAILABLE:
            raise RuntimeError("pycaw library is not installed or available.")

        self._ensure_com()
        speakers = AudioUtilities.GetSpeakers()
        if not speakers:
            raise RuntimeError("No speaker device detected on the system.")

        if hasattr(speakers, "EndpointVolume") and speakers.EndpointVolume is not None:
            return speakers.EndpointVolume

        # Fallback for older pycaw versions
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return cast(interface, POINTER(IAudioEndpointVolume))

    def mute(self) -> bool:
        """Mutes Windows Master Audio. Returns True if successfully muted."""
        with self._lock:
            try:
                vol = self._get_endpoint_volume()
                if vol:
                    self._previous_mute_state = bool(vol.GetMute())
                    vol.SetMute(1, None)
                    logger.info("[AudioControl] Windows Master Audio MUTED (Ducked).")
                    return True
            except Exception as e:
                logger.error(f"[AudioControl Error] Failed to mute master audio: {e}")
            return False

    def unmute(self) -> bool:
        """Unmutes Windows Master Audio. Returns True if successfully unmuted."""
        with self._lock:
            try:
                vol = self._get_endpoint_volume()
                if vol:
                    vol.SetMute(0, None)
                    logger.info("[AudioControl] Windows Master Audio UNMUTED.")
                    return True
            except Exception as e:
                logger.error(f"[AudioControl Error] Failed to unmute master audio: {e}")
            return False

    def is_muted(self) -> bool:
        """Checks whether Windows Master Audio is currently muted."""
        with self._lock:
            try:
                vol = self._get_endpoint_volume()
                if vol:
                    return bool(vol.GetMute())
            except Exception as e:
                logger.error(f"[AudioControl Error] Failed to get mute state: {e}")
            return False

    def set_mute(self, mute_state: bool) -> bool:
        """Sets mute state directly."""
        return self.mute() if mute_state else self.unmute()

    def restore(self) -> bool:
        """Restores previous mute state before mute() was called."""
        if self._previous_mute_state is not None:
            return self.set_mute(self._previous_mute_state)
        return self.unmute()

# Default singleton instance & convenience functions
_controller = WindowsAudioController()

def mute() -> bool:
    return _controller.mute()

def unmute() -> bool:
    return _controller.unmute()

def is_muted() -> bool:
    return _controller.is_muted()

def set_mute(mute_state: bool) -> bool:
    return _controller.set_mute(mute_state)

def restore() -> bool:
    return _controller.restore()
