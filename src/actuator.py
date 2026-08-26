import time
import threading
import winsound
import ctypes
import pyperclip
import keyboard

# Win32 Virtual Key Constants
VK_CONTROL = 0x11
VK_V = 0x56
VK_SPACE = 0x20
KEYEVENTF_KEYUP = 0x0002

def sound_feedback(freq: int, ms: int):
    """Play audio feedback asynchronously."""
    def _beep():
        try:
            winsound.Beep(freq, ms)
        except Exception as e:
            print(f"[Actuator Warning] Sound feedback error: {e}", flush=True)

    threading.Thread(target=_beep, daemon=True).start()

def _send_ctrl_v():
    """Triggers Ctrl+V paste action using Win32 API keybd_event or keyboard module fallback."""
    if hasattr(ctypes, 'windll') and hasattr(ctypes.windll, 'user32'):
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    else:
        keyboard.send('ctrl+v')

def _send_space():
    """Triggers space bar using Win32 API keybd_event or keyboard module fallback."""
    if hasattr(ctypes, 'windll') and hasattr(ctypes.windll, 'user32'):
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_SPACE, 0, 0, 0)
        time.sleep(0.01)
        user32.keybd_event(VK_SPACE, 0, KEYEVENTF_KEYUP, 0)
    else:
        keyboard.send('space')

def paste_text(text: str):
    """
    Injects transcribed text to the active cursor using OS clipboard paste mechanism.
    Steps:
    1. Copy text to clipboard with pyperclip.copy(clean_text)
    2. Pause 0.05s to allow OS clipboard update
    3. Trigger Ctrl+V hotkey via Win32 keybd_event API (VK_CONTROL 0x11 + VK_V 0x56) or keyboard library
    4. Pause 0.02s and press spacebar
    """
    if not text or not text.strip():
        return

    clean_text = text.strip()
    try:
        pyperclip.copy(clean_text)
        time.sleep(0.05)
        _send_ctrl_v()
        time.sleep(0.02)
        _send_space()
        print(f"[Actuator] Successfully pasted text via Win32/keyboard: '{clean_text}'", flush=True)
    except Exception as e:
        print(f"[Actuator Warning] Clipboard paste failed ({e}), falling back to direct keyboard typing...", flush=True)
        try:
            keyboard.write(clean_text + " ")
        except Exception as ex:
            print(f"[Actuator Error] Fallback typing failed: {ex}", flush=True)

# Function aliases for compatibility
type_text = paste_text
inject_to_cursor = paste_text

class TextActuator:
    """Injects text into active cursor position using high-reliability Win32 / keyboard clipboard paste."""

    @staticmethod
    def inject(text: str):
        paste_text(text)

    @staticmethod
    def paste_text(text: str):
        paste_text(text)

    @staticmethod
    def type_text(text: str):
        paste_text(text)
