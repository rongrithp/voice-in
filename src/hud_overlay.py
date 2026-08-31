import enum
import logging
import threading
import time
from typing import Optional
import tkinter as tk

logger = logging.getLogger("HUDOverlay")

class HUDState(enum.Enum):
    IDLE = "idle"
    STT_CONNECTING = "stt_connecting"
    STT_ACTIVE = "stt_active"
    STT_FINALIZING = "stt_finalizing"
    LIVE_CONNECTING = "live_connecting"
    LIVE_ACTIVE = "live_active"
    LIVE_CLOSING = "live_closing"
    TTS_ACTIVE = "tts_active"

STATE_CONFIGS = {
    HUDState.STT_CONNECTING: {
        "text": "🟡 [STT] CONNECTING TO GOOGLE CLOUD...",
        "bg": "#1a1a1a",
        "fg": "#f1c40f",
        "border": "#f1c40f",
    },
    HUDState.STT_ACTIVE: {
        "text": "🔴 [STT STREAMING] MIC ON • SPEAK NOW",
        "bg": "#1a1a1a",
        "fg": "#ffffff",
        "border": "#ff3333",
    },
    HUDState.STT_FINALIZING: {
        "text": "⚪ [STT] INJECTING & FINALIZING...",
        "bg": "#1a1a1a",
        "fg": "#e0e0e0",
        "border": "#ffffff",
    },
    HUDState.LIVE_CONNECTING: {
        "text": "🟡 [GEMINI LIVE] CONNECTING TO MODEL (HANDSHAKE)...",
        "bg": "#1a1a1a",
        "fg": "#f39c12",
        "border": "#f39c12",
    },
    HUDState.LIVE_ACTIVE: {
        "text": "🟢 [GEMINI LIVE] CONNECTED • SCREEN (MON 1) & VOICE READY",
        "bg": "#1a1a1a",
        "fg": "#ffffff",
        "border": "#00e676",
    },
    HUDState.LIVE_CLOSING: {
        "text": "⚪ [GEMINI LIVE] CLOSING SESSION...",
        "bg": "#1a1a1a",
        "fg": "#bdc3c7",
        "border": "#bdc3c7",
    },
    HUDState.TTS_ACTIVE: {
        "text": "🔊 [TTS READING] HIGH-FIDELITY PLAYBACK ACTIVE",
        "bg": "#1a1a1a",
        "fg": "#ffffff",
        "border": "#00bfff",
    },
}

VU_LEVELS = ["▱▱▱▱", "▰▱▱▱", "▰▰▱▱", "▰▰▰▱", "▰▰▰▰"]

class HUDOverlay:
    """
    Ultra-lightweight, frameless, topmost, click-through On-Screen Floating Pill HUD.
    Displays granular real-time connection lifecycle states and live audio ingestion meters
    at the top-center of Monitor 1 (UltraWide 3440x1440) whenever STT (F13) or Gemini Live (F20)
    is active. Guarantees 100% visibility to prevent unintended API billing waste.
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
        self._current_rms = 0.0
        self._last_ui_update_time = 0.0

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
            self.root.attributes("-alpha", 0.96)
            self.root.configure(bg="#0a0a0a")

            # Click-through and non-activating window attributes on Windows OS
            self._force_click_through_and_topmost()

            # Pill container frame with high-contrast glowing border
            self._pill_frame = tk.Frame(
                self.root,
                bg="#1a1a1a",
                highlightthickness=2,
                highlightbackground="#333333",
                padx=20,
                pady=7
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

    def _force_click_through_and_topmost(self):
        """Applies Win32 WS_EX_TRANSPARENT, WS_EX_NOACTIVATE and HWND_TOPMOST."""
        if not self.root:
            return
        try:
            hwnd = self.root.winfo_id()
            if not isinstance(hwnd, int):
                return
            import ctypes
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

            HWND_TOPMOST = -1
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010
            SWP_SHOWWINDOW = 0x0040
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW
            )
        except Exception as ex:
            logger.debug(f"[HUD Win32 Notice] {ex}")

    def _reposition(self):
        """Positions the floating HUD pill precisely at top-center of Monitor 1 (UltraWide 3440x1440)."""
        if not self.root:
            return
        try:
            self.root.update_idletasks()
            w = max(420, self.root.winfo_reqwidth() + 28)
            h = max(44, self.root.winfo_reqheight() + 10)

            # Resolve Monitor 1 dimensions (Default: UltraWide 3440x1440 at 0,0)
            mon1 = None
            try:
                from src.screen_capture import get_monitor_dict
                mon1 = get_monitor_dict(1)
            except Exception:
                pass

            if mon1:
                mon_left = int(mon1.get("left", 0))
                mon_top = int(mon1.get("top", 0))
                mon_width = int(mon1.get("width", 3440))
            else:
                mon_left = 0
                mon_top = 0
                try:
                    mon_width = int(self.root.winfo_screenwidth() or 3440)
                except Exception:
                    mon_width = 3440

            if self.position == "top-right":
                x = mon_left + mon_width - w - 24
                y = mon_top + 20
            else: # top-center
                x = mon_left + (mon_width - w) // 2
                y = mon_top + 20

            self.root.geometry(f"{w}x{h}+{x}+{y}")
        except Exception:
            pass

    def _schedule_pulse(self):
        """Dynamic border pulsing animation while active to catch peripheral vision."""
        if not self.root or not self._is_running:
            return
        try:
            if self._is_visible and self.state in STATE_CONFIGS:
                cfg = STATE_CONFIGS[self.state]
                self._pulse_toggle = not self._pulse_toggle
                border_color = cfg["border"] if self._pulse_toggle else "#2a2a2a"
                if self._pill_frame:
                    self._pill_frame.configure(highlightbackground=border_color)
        except Exception:
            pass
        self._pulse_job = self.root.after(450, self._schedule_pulse)

    def update_audio_level(self, rms: float):
        """Updates live audio RMS level and reflects real-time audio transmission on HUD."""
        self._current_rms = rms
        now = time.perf_counter()
        if now - self._last_ui_update_time < 0.10:  # Throttle to max 10 FPS for ultra-smooth UI
            return
        self._last_ui_update_time = now

        if not self.root or not self._is_running or not self._is_visible:
            return

        # Calculate VU meter level (0 to 4)
        if rms < 150.0:
            vu_idx = 0
        elif rms < 500.0:
            vu_idx = 1
        elif rms < 1500.0:
            vu_idx = 2
        elif rms < 3000.0:
            vu_idx = 3
        else:
            vu_idx = 4

        vu_meter = VU_LEVELS[vu_idx]
        self.root.after(0, lambda: self._apply_audio_level(vu_meter))

    def _apply_audio_level(self, vu_meter: str):
        if not self.root or not self._is_running or not self._is_visible:
            return
        try:
            if self.state in (HUDState.STT_ACTIVE, HUDState.LIVE_ACTIVE):
                cfg = STATE_CONFIGS[self.state]
                txt = f"{cfg['text']}  {vu_meter}"
                if self._label:
                    self._label.configure(text=txt)
        except Exception:
            pass

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
                    self._is_visible = True
                
                self.root.attributes("-topmost", True)
                self.root.lift()
                self._force_click_through_and_topmost()
                self._reposition()
        except Exception as e:
            logger.debug(f"[HUD Apply Notice] {e}")

    # --- Granular Helper Methods ---

    def show_stt_connecting(self):
        """🟡 [STT] CONNECTING TO GOOGLE CLOUD..."""
        self.set_state(HUDState.STT_CONNECTING)

    def show_stt(self):
        """🔴 [STT STREAMING] MIC ON • SPEAK NOW"""
        self.set_state(HUDState.STT_ACTIVE)

    def show_stt_finalizing(self):
        """⚪ [STT] INJECTING & FINALIZING..."""
        self.set_state(HUDState.STT_FINALIZING)

    def show_live_connecting(self):
        """🟡 [GEMINI LIVE] CONNECTING TO MODEL (HANDSHAKE)..."""
        self.set_state(HUDState.LIVE_CONNECTING)

    def show_live(self):
        """🟢 [GEMINI LIVE] CONNECTED • SCREEN (MON 1) & VOICE READY"""
        self.set_state(HUDState.LIVE_ACTIVE)

    def show_live_closing(self):
        """⚪ [GEMINI LIVE] CLOSING SESSION..."""
        self.set_state(HUDState.LIVE_CLOSING)

    def show_tts(self):
        """🔊 [TTS READING] HIGH-FIDELITY PLAYBACK ACTIVE"""
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
