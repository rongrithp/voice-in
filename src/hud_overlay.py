import enum
import logging
import threading
import time
from typing import Optional
import tkinter as tk

logger = logging.getLogger("HUDOverlay")

class HUDState(enum.Enum):
    IDLE = "idle"
    STT_ACTIVE = "stt_active"
    LIVE_ACTIVE = "live_active"
    TTS_ACTIVE = "tts_active"

STATE_CONFIGS = {
    HUDState.STT_ACTIVE: {
        "text": "🔴 STT LISTENING...",
        "bg": "#c0392b",
        "fg": "#ffffff",
        "border": "#e74c3c",
    },
    HUDState.LIVE_ACTIVE: {
        "text": "🟢 GEMINI LIVE CONNECTED",
        "bg": "#1e824c",
        "fg": "#ffffff",
        "border": "#2ecc71",
    },
    HUDState.TTS_ACTIVE: {
        "text": "🔊 TTS READING...",
        "bg": "#2980b9",
        "fg": "#ffffff",
        "border": "#3498db",
    },
}

class HUDOverlay:
    """
    Ultra-lightweight, frameless, topmost, click-through On-Screen Floating Pill HUD.
    Displays vibrant visual indicator at the top of the screen whenever STT (F13)
    or Gemini Live Co-pilot (F20) is actively ingesting audio to prevent billing waste.
    """

    def __init__(self, position: str = "top-center"):
        self.position = position
        self.state = HUDState.IDLE
        self.root: Optional[tk.Tk] = None
        self._pill_frame: Optional[tk.Frame] = None
        self._label: Optional[tk.Label] = None
        self._is_running = False
        self._is_visible = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._pulse_job = None
        self._pulse_toggle = False

    def start(self):
        """Starts the HUD overlay in a dedicated daemon thread."""
        with self._lock:
            if self._is_running:
                return
            self._is_running = True
            self._thread = threading.Thread(target=self._run_ui_loop, daemon=True, name="HUDOverlayThread")
            self._thread.start()

    def _run_ui_loop(self):
        try:
            self.root = tk.Tk()
            self.root.title("VoiceHubHUD")
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", 0.95)
            self.root.configure(bg="#111111")

            # Click-through and non-activating window attributes on Windows OS
            try:
                import ctypes
                hwnd = self.root.winfo_id()
                GWL_EXSTYLE = -20
                WS_EX_TRANSPARENT = 0x00000020
                WS_EX_LAYERED = 0x00080000
                WS_EX_TOOLWINDOW = 0x00000080
                WS_EX_NOACTIVATE = 0x08000000
                
                ctypes.windll.user32.SetWindowLongW(
                    hwnd,
                    GWL_EXSTYLE,
                    WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
                )
            except Exception as ex:
                logger.debug(f"[HUD ClickThrough Notice] {ex}")

            # Pill container
            self._pill_frame = tk.Frame(
                self.root,
                bg="#1a1a1a",
                highlightthickness=2,
                highlightbackground="#333333",
                padx=16,
                pady=6
            )
            self._pill_frame.pack(fill="both", expand=True)

            self._label = tk.Label(
                self._pill_frame,
                text="● READY",
                font=("Segoe UI", 11, "bold"),
                bg="#1a1a1a",
                fg="#ffffff",
                padx=8,
                pady=2
            )
            self._label.pack()

            # Initially hidden in IDLE state
            self.root.withdraw()
            self._is_visible = False

            self._reposition()
            self._schedule_pulse()
            self.root.mainloop()
        except Exception as e:
            logger.error(f"[HUD Error] UI loop error: {e}")
        finally:
            self._is_running = False

    def _reposition(self):
        """Positions the floating HUD pill at top-center of primary screen."""
        if not self.root:
            return
        try:
            self.root.update_idletasks()
            sw = self.root.winfo_screenwidth()
            w = max(260, self.root.winfo_reqwidth() + 20)
            h = max(40, self.root.winfo_reqheight() + 10)
            
            if self.position == "top-right":
                x = sw - w - 24
                y = 18
            else: # top-center
                x = (sw - w) // 2
                y = 14

            self.root.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _schedule_pulse(self):
        """Micro-pulse effect on the active indicator to catch peripheral vision."""
        if not self.root or not self._is_running:
            return
        try:
            if self._is_visible and self.state in STATE_CONFIGS:
                cfg = STATE_CONFIGS[self.state]
                self._pulse_toggle = not self._pulse_toggle
                border_color = cfg["border"] if self._pulse_toggle else cfg["bg"]
                if self._pill_frame:
                    self._pill_frame.configure(highlightbackground=border_color)
        except Exception:
            pass
        self._pulse_job = self.root.after(600, self._schedule_pulse)

    def set_state(self, state: HUDState, custom_text: Optional[str] = None):
        """Thread-safe state update."""
        self.state = state
        if not self.root or not self._is_running:
            return
        self.root.after(0, lambda: self._apply_state(state, custom_text))

    def _apply_state(self, state: HUDState, custom_text: Optional[str] = None):
        if not self.root or not self._is_running:
            return

        try:
            if state == HUDState.IDLE:
                if self._is_visible:
                    self.root.withdraw()
                    self._is_visible = False
            else:
                cfg = STATE_CONFIGS.get(state, STATE_CONFIGS[HUDState.STT_ACTIVE])
                txt = custom_text or cfg["text"]

                if self._pill_frame:
                    self._pill_frame.configure(bg=cfg["bg"], highlightbackground=cfg["border"])
                if self._label:
                    self._label.configure(text=txt, bg=cfg["bg"], fg=cfg["fg"])

                if not self._is_visible:
                    self.root.deiconify()
                    self.root.attributes("-topmost", True)
                    self._is_visible = True
                
                self._reposition()
        except Exception as e:
            logger.debug(f"[HUD Apply Notice] {e}")

    def show_stt(self):
        """Displays prominent red STT recording pill."""
        self.set_state(HUDState.STT_ACTIVE)

    def show_live(self):
        """Displays prominent green Gemini Live streaming pill."""
        self.set_state(HUDState.LIVE_ACTIVE)

    def show_tts(self):
        """Displays blue TTS playback pill."""
        self.set_state(HUDState.TTS_ACTIVE)

    def hide(self):
        """Hides the HUD completely when idle."""
        self.set_state(HUDState.IDLE)

    def stop(self):
        """Terminates HUD overlay."""
        with self._lock:
            self._is_running = False
            if self.root:
                try:
                    self.root.after(0, self.root.destroy)
                except Exception:
                    pass
                self.root = None
