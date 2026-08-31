import logging
import threading
from typing import Optional
import tkinter as tk

logger = logging.getLogger("ScreenBorderOverlay")

class ScreenBorderOverlay:
    """
    Ultra-lightweight, frameless, topmost, click-through Neon Green Glowing Border Overlay.
    Draws a thin 3px neon indicator around the perimeter of Monitor 1 (UltraWide 3440x1440)
    during active Gemini Live (F20) multimodal co-pilot sessions, visually confirming that
    screen vision ingestion is active without any intrusive preview windows.
    """

    def __init__(self, border_width: int = 3, border_color: str = "#00ff66"):
        self.border_width = border_width
        self.border_color = border_color
        self.root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._is_running = False
        self._is_visible = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Starts the border overlay in a dedicated daemon thread."""
        with self._lock:
            if self._is_running:
                return
            self._is_running = True
            self._thread = threading.Thread(target=self._run_ui_loop, daemon=True, name="ScreenBorderThread")
            self._thread.start()

    def _run_ui_loop(self):
        try:
            self.root = tk.Tk()
            self.root.title("VoiceHubScreenBorder")
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            
            # Use color-key transparency so the inner desktop is 100% visible and responsive
            bg_color = "#010101"
            self.root.configure(bg=bg_color)
            try:
                self.root.wm_attributes("-transparentcolor", bg_color)
            except Exception:
                pass

            # Win32 Click-Through and Non-Activating Window Styles
            self._force_click_through()

            # Canvas for border outline
            self._canvas = tk.Canvas(
                self.root,
                bg=bg_color,
                highlightthickness=0,
                bd=0
            )
            self._canvas.pack(fill="both", expand=True)

            self._reposition_and_draw()

            # Initially hidden until show() is invoked
            self.root.withdraw()
            self._is_visible = False

            self.root.mainloop()
        except Exception as e:
            logger.error(f"[ScreenBorder Error] UI loop error: {e}")
        finally:
            self._is_running = False

    def _force_click_through(self):
        """Applies Win32 WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, and WS_EX_NOACTIVATE."""
        if not self.root:
            return
        try:
            hwnd = self.root.winfo_id()
            if not isinstance(hwnd, int):
                return
            import ctypes
            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000
            
            old_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            new_style = old_style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)

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
            logger.debug(f"[ScreenBorder Win32 Notice] {ex}")

    def _reposition_and_draw(self):
        """Positions overlay to match Monitor 1 coordinates and draws glowing border perimeter."""
        if not self.root or not self._canvas:
            return
        try:
            mon1 = None
            try:
                from src.screen_capture import get_monitor_dict
                mon1 = get_monitor_dict(1)
            except Exception:
                pass

            if mon1 and mon1.get("width") and mon1.get("height"):
                mon_left = int(mon1.get("left", 0))
                mon_top = int(mon1.get("top", 0))
                mon_width = int(mon1.get("width", 3440))
                mon_height = int(mon1.get("height", 1440))
            else:
                mon_left = 0
                mon_top = 0
                mon_width = 3440
                mon_height = 1440

            self.root.geometry(f"{mon_width}x{mon_height}+{mon_left}+{mon_top}")
            self._canvas.delete("all")

            # Draw outer rectangle border
            b = self.border_width
            self._canvas.create_rectangle(
                b // 2,
                b // 2,
                mon_width - (b // 2),
                mon_height - (b // 2),
                outline=self.border_color,
                width=b
            )
        except Exception as ex:
            logger.debug(f"[ScreenBorder Reposition Notice] {ex}")

    def show(self):
        """Displays the neon screen border on Monitor 1."""
        if not self.root or not self._is_running:
            return
        self.root.after(0, self._apply_show)

    def _apply_show(self):
        if not self.root or not self._is_running:
            return
        try:
            self._reposition_and_draw()
            if not self._is_visible:
                self.root.deiconify()
                self._is_visible = True
            self.root.attributes("-topmost", True)
            self.root.lift()
            self._force_click_through()
        except Exception as ex:
            logger.debug(f"[ScreenBorder Show Notice] {ex}")

    def hide(self):
        """Hides the screen border."""
        if not self.root or not self._is_running:
            return
        self.root.after(0, self._apply_hide)

    def _apply_hide(self):
        if not self.root or not self._is_running:
            return
        try:
            if self._is_visible:
                self.root.withdraw()
                self._is_visible = False
        except Exception as ex:
            logger.debug(f"[ScreenBorder Hide Notice] {ex}")

    def stop(self):
        """Terminates screen border overlay."""
        with self._lock:
            self._is_running = False
            if self.root:
                try:
                    self.root.after(0, self.root.destroy)
                except Exception:
                    pass
                self.root = None
