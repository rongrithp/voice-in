import ctypes
import logging
import threading
from typing import Optional
import tkinter as tk

# Ensure the process enables per-monitor DPI awareness at startup
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

logger = logging.getLogger("ScreenBorderOverlay")

class ScreenBorderOverlay:
    """
    Ultra-lightweight, frameless, topmost, click-through Neon Green Glowing Border Overlay.
    Draws a thin neon indicator around the perimeter of the active monitor
    during active Gemini Live (F20) multimodal co-pilot sessions, visually confirming that
    screen vision ingestion is active without capturing mouse clicks or preview windows.
    """

    TRANS_COLOR = "#010000"  # Near-black colorkey (RGB 1, 0, 0) for native Windows transparency

    def __init__(self, border_width: int = 4, border_color: str = "#00FF00"):
        self.border_width = border_width
        self.border_color = border_color
        self.root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._is_running = False
        self._is_visible = False
        self._lock = threading.Lock()
        self._ready_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._current_monitor_key: Optional[tuple] = None  # (left, top, width, height)
        self._poll_active: bool = False

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
            
            # Native Tkinter Transparency Mechanism
            self.root.config(bg=self.TRANS_COLOR)
            try:
                self.root.attributes("-transparentcolor", self.TRANS_COLOR)
                self.root.attributes("-alpha", 1.0)
            except Exception:
                pass

            # Win32 Click-Through and Non-Activating Window Styles
            self._force_click_through()

            # Canvas for border outline
            self._canvas = tk.Canvas(
                self.root,
                bg=self.TRANS_COLOR,
                highlightthickness=0,
                bd=0
            )
            self._canvas.pack(fill="both", expand=True)

            self._snap_to_monitor()

            # Initially hidden until show() is invoked
            self.root.withdraw()
            self._is_visible = False

            # Signal that the root window and UI thread are fully initialized and ready
            self._ready_event.set()

            # Run continuous monitor polling loop on dedicated UI thread
            self.root.after(50, self._poll_cursor)

            self.root.mainloop()
        except Exception as e:
            logger.error(f"[ScreenBorder Error] UI loop error: {e}")
        finally:
            self._is_running = False
            self._ready_event.clear()

    def _force_click_through(self):
        """Applies Win32 WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_TOOLWINDOW, and WS_EX_NOACTIVATE to parent HWND."""
        if not self.root:
            return
        try:
            raw_id = self.root.winfo_id()
            if not isinstance(raw_id, int):
                return
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(raw_id)
            if not hwnd:
                hwnd = raw_id

            GWL_EXSTYLE = -20
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_LAYERED = 0x00080000
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000
            
            current_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd,
                GWL_EXSTYLE,
                current_style | WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            )

            # Explicitly set layered window colorkey transparency
            LWA_COLORKEY = 0x00000001
            colorkey = 0x00000001  # 0x00BBGGRR -> RGB(1, 0, 0)
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, colorkey, 0, LWA_COLORKEY)

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

    def _get_cursor_monitor(self) -> Optional[dict]:
        """
        Returns physical monitor dict containing the cursor using win32api / ctypes Win32 calls.
        Handles non-uniform multi-monitor layouts and negative/positive offsets.
        """
        try:
            import win32api
            pt = win32api.GetCursorPos()
            # MONITOR_DEFAULTTONEAREST = 2
            h_mon = win32api.MonitorFromPoint(pt, 2)
            if h_mon:
                info = win32api.GetMonitorInfo(h_mon)
                rc = info.get("Monitor")
                if rc:
                    left, top, right, bottom = rc
                    return {
                        "left": int(left),
                        "top": int(top),
                        "width": int(right - left),
                        "height": int(bottom - top)
                    }
        except Exception:
            pass

        try:
            import ctypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_ulong),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", ctypes.c_ulong)
                ]

            pt = POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                MONITOR_DEFAULTTONEAREST = 2
                h_mon = ctypes.windll.user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
                if h_mon:
                    mi = MONITORINFO()
                    mi.cbSize = ctypes.sizeof(MONITORINFO)
                    if ctypes.windll.user32.GetMonitorInfoW(h_mon, ctypes.byref(mi)):
                        rc = mi.rcMonitor
                        return {
                            "left": int(rc.left),
                            "top": int(rc.top),
                            "width": int(rc.right - rc.left),
                            "height": int(rc.bottom - rc.top)
                        }
        except Exception as exc:
            logger.debug(f"[ScreenBorder] _get_cursor_monitor error: {exc}")

        # Fallback to enumerating physical monitors
        try:
            import win32api
            for h_mon, _, _ in win32api.EnumDisplayMonitors():
                info = win32api.GetMonitorInfo(h_mon)
                rc = info.get("Monitor")
                if rc:
                    left, top, right, bottom = rc
                    if info.get("Flags", 0) & 1:  # Primary monitor
                        return {
                            "left": int(left),
                            "top": int(top),
                            "width": int(right - left),
                            "height": int(bottom - top)
                        }
        except Exception:
            pass

        return None

    def _snap_to_monitor(self, mon_dict: Optional[dict] = None):
        """
        Positions overlay to match the target display bounds and redraws the perimeter border.
        1. Updates geometry using exact display rect: f"{width}x{height}+{left}+{top}".
        2. Ensures window style flags remain transparent and click-through.
        3. Clears and redraws the border perimeter directly along (0, 0, width, height).
        4. Forces synchronous window update via root.update_idletasks().
        5. If overlay is intended to be shown (self._is_visible), ensures deiconify and topmost are applied.
        """
        if not self.root or not self._canvas:
            return
        try:
            mon = mon_dict
            if mon is None:
                mon = self._get_cursor_monitor()
            if mon is None:
                try:
                    from src.screen_capture import get_monitor_dict
                    mon = get_monitor_dict(1)
                except Exception:
                    pass

            if mon and mon.get("width") and mon.get("height"):
                mon_left = int(mon.get("left", 0))
                mon_top = int(mon.get("top", 0))
                mon_width = int(mon.get("width", 3440))
                mon_height = int(mon.get("height", 1440))
            else:
                mon_left = 0
                mon_top = 0
                mon_width = 3440
                mon_height = 1440

            self._current_monitor_key = (mon_left, mon_top, mon_width, mon_height)

            # 1. Update geometry using exact display rect: f"{width}x{height}+{left}+{top}"
            self.root.geometry(f"{mon_width}x{mon_height}+{mon_left}+{mon_top}")
            self._canvas.config(width=mon_width, height=mon_height)

            # 2. Ensure window style flags remain transparent and click-through
            self._force_click_through()

            # 3. Clear and redraw the border perimeter directly along outer perimeter (0, 0, width, height)
            self._canvas.delete("all")
            b = self.border_width
            half_b = b // 2
            self._canvas.create_rectangle(
                half_b,
                half_b,
                mon_width - half_b,
                mon_height - half_b,
                outline=self.border_color,
                width=b
            )

            # 4. Force synchronous window update via root.update_idletasks()
            self.root.update_idletasks()

            # 5. Always ensure self.root.deiconify() and self._is_visible = True run if intended to be shown
            if self._is_visible:
                self.root.deiconify()
                self.root.attributes("-topmost", True)
                self.root.attributes("-alpha", 1.0)
                self.root.lift()
                self._force_click_through()
        except Exception as ex:
            logger.debug(f"[ScreenBorder Reposition Notice] {ex}")

    def _reposition_and_draw(self, mon_dict: Optional[dict] = None):
        """Backwards-compatible alias for _snap_to_monitor."""
        return self._snap_to_monitor(mon_dict=mon_dict)

    def snap_to_cursor_monitor(self):
        """Immediately repositions the border to whichever monitor currently holds the cursor."""
        if not self.root or not self._is_running:
            return
        self.root.after(0, self._do_snap_to_cursor_monitor)

    def _do_snap_to_cursor_monitor(self):
        """tkinter-thread implementation of snap_to_cursor_monitor."""
        mon = self._get_cursor_monitor()
        if mon is not None:
            self._snap_to_monitor(mon_dict=mon)
            self.root.attributes("-topmost", True)
            self.root.lift()
            self._force_click_through()

    def _poll_cursor(self):
        """
        Continuous 50 ms polling loop running on dedicated UI thread via root.after(50, self._poll_cursor).
        When the cursor coordinates move across monitor boundaries and overlay is visible,
        triggers _snap_to_monitor, updating Tkinter geometry and re-drawing canvas borders without blocking.
        """
        if not self._is_running or not self.root:
            return
        try:
            if self._is_visible:
                mon = self._get_cursor_monitor()
                if mon is not None:
                    key = (int(mon.get("left", 0)), int(mon.get("top", 0)), int(mon.get("width", 0)), int(mon.get("height", 0)))
                    if key != self._current_monitor_key:
                        self._snap_to_monitor(mon_dict=mon)
                        self.root.attributes("-topmost", True)
                        self.root.lift()
                        self._force_click_through()
        except Exception as exc:
            logger.debug(f"[ScreenBorder Poll Notice] {exc}")
        finally:
            if self._is_running and self.root:
                try:
                    self.root.after(50, self._poll_cursor)
                except Exception:
                    pass

    # Backwards-compatibility alias
    _poll_cursor_monitor = _poll_cursor

    def show(self):
        """Displays the neon screen border on whichever monitor currently holds the cursor."""
        if not self._is_running:
            self.start()
        if self._thread is not None and not self._ready_event.is_set():
            if not self._ready_event.wait(timeout=2.0):
                logger.warning("[ScreenBorder] UI thread did not become ready within 2.0s.")
                return
        if not self.root:
            return
        self._is_visible = True
        self.root.after(0, self._apply_show)

    def _apply_show(self):
        if not self.root or not self._is_running:
            return
        try:
            self._is_visible = True
            # Snap to cursor monitor immediately on show
            mon = self._get_cursor_monitor()
            self._snap_to_monitor(mon_dict=mon)
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", 1.0)
            self.root.lift()
            self._force_click_through()
            self.root.update_idletasks()
        except Exception as ex:
            logger.debug(f"[ScreenBorder Show Notice] {ex}")

    def hide(self):
        """Hides the screen border."""
        self._is_visible = False
        if not self.root or not self._is_running:
            return
        self.root.after(0, self._apply_hide)

    def _apply_hide(self):
        if not self.root or not self._is_running:
            return
        try:
            self._is_visible = False
            self.root.withdraw()
        except Exception as ex:
            logger.debug(f"[ScreenBorder Hide Notice] {ex}")

    def stop(self):
        """Terminates screen border overlay."""
        with self._lock:
            self._is_running = False
            self._is_visible = False
            self._ready_event.clear()
            if self.root:
                try:
                    self.root.after(0, self.root.destroy)
                except Exception:
                    pass
                self.root = None
