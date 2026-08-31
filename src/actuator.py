import time
import threading
import winsound
import ctypes
import pyperclip
import keyboard

# Disable console Ctrl+C termination on Windows for injected keybd events
if hasattr(ctypes, 'windll') and hasattr(ctypes.windll, 'kernel32'):
    try:
        ctypes.windll.kernel32.SetConsoleCtrlHandler(None, True)
    except Exception:
        pass

# Win32 Virtual Key Constants
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_END = 0x23
VK_C = 0x43
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

def set_clipboard_text(text: str, max_retries: int = 3) -> bool:
    """Sets text to OS clipboard using win32clipboard if available, with pyperclip fallback and retries."""
    for attempt in range(max_retries):
        # 1. Try win32clipboard
        try:
            import win32clipboard
            import win32con
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            return True
        except ImportError:
            pass
        except Exception:
            try:
                import win32clipboard
                win32clipboard.CloseClipboard()
            except Exception:
                pass

        # 2. Try pyperclip fallback
        try:
            pyperclip.copy(text)
            return True
        except Exception:
            time.sleep(0.015)

    return False

def get_clipboard_text() -> str:
    """Retrieves text from OS clipboard with win32clipboard and pyperclip fallbacks."""
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            return data or ""
        win32clipboard.CloseClipboard()
    except Exception:
        pass

    try:
        return pyperclip.paste() or ""
    except Exception:
        return ""

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

def _send_shift_ctrl_end():
    """Simulates Shift + Ctrl + End to highlight from cursor to document/text end."""
    if hasattr(ctypes, 'windll') and hasattr(ctypes.windll, 'user32'):
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_SHIFT, 0, 0, 0)
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_END, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_END, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
    else:
        keyboard.send('shift+ctrl+end')

def _send_ctrl_c():
    """Simulates Ctrl + C to copy highlighted text into clipboard."""
    if hasattr(ctypes, 'windll') and hasattr(ctypes.windll, 'user32'):
        user32 = ctypes.windll.user32
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_C, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    else:
        keyboard.send('ctrl+c')

def _direct_type_fallback(text_with_space: str):
    """Direct typing fallback using keyboard module or pynput."""
    try:
        keyboard.write(text_with_space)
        print(f"[Actuator Fallback] Typed directly via keyboard module: '{text_with_space.strip()}'", flush=True)
    except Exception as e:
        try:
            from pynput.keyboard import Controller
            controller = Controller()
            controller.type(text_with_space)
            print(f"[Actuator Fallback] Typed directly via pynput: '{text_with_space.strip()}'", flush=True)
        except Exception as ex:
            print(f"[Actuator Error] Direct typing fallback failed: {ex}", flush=True)

def copy_selected_text(wait_seconds: float = 0.05) -> str:
    """
    Copies whatever text is already highlighted/selected by the user via Ctrl + C directly
    (without pressing Shift+Ctrl+End) and returns the clipboard content.
    """
    try:
        # 1. Simulate Ctrl + C directly
        _send_ctrl_c()
        time.sleep(wait_seconds)

        # 2. Extract copied text
        text = get_clipboard_text()
        if text:
            print(f"[Actuator] Successfully copied {len(text)} characters of selected text.", flush=True)
            return text
        return ""
    except Exception as e:
        print(f"[Actuator Error] Failed to copy selected text: {e}", flush=True)
        return ""

def copy_cursor_to_bottom(wait_seconds: float = 0.08) -> str:
    """
    Selects text from cursor to bottom with Shift + Ctrl + End,
    waits ~80ms, copies via Ctrl + C, and returns the clipboard content.
    """
    try:
        # 1. Simulate Shift + Ctrl + End
        _send_shift_ctrl_end()
        time.sleep(wait_seconds)

        # 2. Simulate Ctrl + C
        _send_ctrl_c()
        time.sleep(0.05)

        # 3. Extract copied text
        text = get_clipboard_text()
        if text:
            print(f"[Actuator] Successfully copied {len(text)} characters from cursor to bottom.", flush=True)
            return text
        return ""
    except Exception as e:
        print(f"[Actuator Error] Failed to copy text from cursor: {e}", flush=True)
        return ""

def paste_text(text: str, add_space: bool = True):
    """
    Injects transcribed text to the active cursor using OS clipboard paste (Ctrl+V) as Primary Priority.
    Guarantees 100% Thai Unicode integrity (vowels, tone marks, NFC/NFD).
    Falls back to direct typing (keyboard/pynput) only if OS clipboard raises an exception.
    """
    if not text:
        return

    clean_text = text.strip() if not text.startswith(" ") else " " + text.strip()
    if not clean_text:
        return

    pasted = False
    try:
        if set_clipboard_text(clean_text):
            # 30ms settling delay for OS clipboard subsystem
            time.sleep(0.03)
            _send_ctrl_v()
            if add_space:
                time.sleep(0.015)
                _send_space()
            pasted = True
            print(f"[Actuator] Injected text via Clipboard (Ctrl+V): '{clean_text}'", flush=True)
        else:
            raise RuntimeError("Clipboard set returned False")
    except Exception as e:
        print(f"[Actuator Warning] Clipboard paste failed ({e}), falling back to direct typing...", flush=True)

    if not pasted:
        suffix = " " if add_space else ""
        _direct_type_fallback(clean_text + suffix)

def inject_delta_text(delta_text: str):
    """
    Injects incremental delta words to active cursor using Clipboard Paste (Ctrl+V) priority.
    Preserves Thai Unicode tone marks and vowels with zero character corruption.
    """
    if not delta_text:
        return
    paste_text(delta_text, add_space=False)

# Function aliases for compatibility
type_text = paste_text
inject_to_cursor = paste_text

class TextActuator:
    """Injects text into active cursor position using high-reliability Win32 / keyboard clipboard paste."""

    @staticmethod
    def inject(text: str):
        paste_text(text)

    @staticmethod
    def inject_delta(delta_text: str):
        inject_delta_text(delta_text)

    @staticmethod
    def paste_text(text: str):
        paste_text(text)

    @staticmethod
    def type_text(text: str):
        paste_text(text)

    @staticmethod
    def copy_selected(wait_seconds: float = 0.05) -> str:
        return copy_selected_text(wait_seconds)

    @staticmethod
    def copy_from_cursor(wait_seconds: float = 0.08) -> str:
        return copy_cursor_to_bottom(wait_seconds)
