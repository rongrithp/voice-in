"""
Floating Robot Indicator Widget for Gemini Multimodal Live Co-pilot.
Always-on-Top, draggable across multi-monitor setups, displaying real-time traffic LED states:
- IDLE / STANDBY: Solid Bright Green (#00FF00) — App active, session connected and idle (zero traffic)
- ACTIVE DATA STREAMING: Pulsing/Blinking Green (alternating #00FF00 and #003300 every 200ms) — Audio/vision packets in flight
- DISCONNECTED / INACTIVE: Dim Charcoal (#333333) — Copilot stopped / offline
"""

import enum
import logging
import threading
from typing import Optional
import tkinter as tk

logger = logging.getLogger("RobotHUDWidget")


class RobotLEDState(enum.Enum):
    INACTIVE = "inactive"      # Dim Charcoal (#333333) - Copilot stopped / offline
    IDLE = "idle"              # Solid Bright Green (#00FF00) - Session connected & idle
    STREAMING = "streaming"    # Pulsing/Blinking Green (#00FF00 <-> #003300 every 200ms)

    # Backwards-compatible aliases
    OFF = "inactive"
    TRANSMITTING = "streaming"


class RobotHUDWidget:
    """
    Compact, draggable, topmost floating robot indicator widget (approx 56x56 px).
    """

    TRANS_COLOR = "#000001"  # Near-black colorkey for transparency

    def __init__(
        self,
        size: int = 56,
        initial_x: Optional[int] = None,
        initial_y: Optional[int] = None
    ):
        self.size = size
        self.initial_x = initial_x
        self.initial_y = initial_y

        self.root: Optional[tk.Tk] = None
        self._canvas: Optional[tk.Canvas] = None
        self._is_running = False
        self._is_visible = False
        self._state = RobotLEDState.INACTIVE
        self._blink_phase = False
        self._blink_timer_id: Optional[str] = None

        self._lock = threading.Lock()
        self._ready_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Drag tracking
        self._drag_start_x = 0
        self._drag_start_y = 0

    @property
    def state(self) -> RobotLEDState:
        return self._state

    def start(self):
        """Starts the robot widget in a dedicated background daemon thread."""
        with self._lock:
            if self._is_running:
                return
            self._is_running = True
            self._thread = threading.Thread(
                target=self._run_ui_loop,
                daemon=True,
                name="RobotHUDThread"
            )
            self._thread.start()

    def _run_ui_loop(self):
        try:
            self.root = tk.Tk()
            self.root.title("VoiceHubRobotWidget")
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)

            # Colorkey transparency
            self.root.config(bg=self.TRANS_COLOR)
            try:
                self.root.attributes("-transparentcolor", self.TRANS_COLOR)
                self.root.attributes("-alpha", 1.0)
            except Exception:
                pass

            # Non-activating & toolwindow styles so it doesn't steal focus from active windows
            self._apply_window_styles()

            # Set initial position (default: bottom-right above taskbar)
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            pos_x = self.initial_x if self.initial_x is not None else max(10, screen_w - self.size - 24)
            pos_y = self.initial_y if self.initial_y is not None else max(10, screen_h - self.size - 100)
            self.root.geometry(f"{self.size}x{self.size}+{pos_x}+{pos_y}")

            # Canvas for Vector Robot Rendering
            self._canvas = tk.Canvas(
                self.root,
                width=self.size,
                height=self.size,
                bg=self.TRANS_COLOR,
                highlightthickness=0,
                bd=0
            )
            self._canvas.pack(fill="both", expand=True)

            # Draggable bindings across multi-monitor setups
            self._canvas.bind("<Button-1>", self._on_drag_start)
            self._canvas.bind("<B1-Motion>", self._on_drag_motion)

            # Draw initial state
            self._draw_robot(self._get_led_color())

            self._ready_event.set()
            self._is_visible = True

            self.root.mainloop()
        except Exception as e:
            logger.error(f"[RobotHUD Error] UI loop error: {e}")
        finally:
            self._is_running = False
            self._ready_event.clear()

    def _apply_window_styles(self):
        """Applies Win32 WS_EX_TOOLWINDOW and WS_EX_NOACTIVATE without WS_EX_TRANSPARENT (draggable)."""
        if not self.root:
            return
        try:
            raw_id = self.root.winfo_id()
            if not isinstance(raw_id, int):
                return
            import ctypes
            hwnd = ctypes.windll.user32.GetParent(raw_id) or raw_id
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000

            current_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd,
                GWL_EXSTYLE,
                current_style | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
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
            logger.debug(f"[RobotHUD Win32 Notice] {ex}")

    def _on_drag_start(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_drag_motion(self, event):
        if not self.root:
            return
        dx = event.x - self._drag_start_x
        dy = event.y - self._drag_start_y
        cur_x = self.root.winfo_x()
        cur_y = self.root.winfo_y()
        self.root.geometry(f"+{cur_x + dx}+{cur_y + dy}")

    def _get_led_color(self) -> str:
        if self._state in (RobotLEDState.INACTIVE, RobotLEDState.OFF):
            return "#333333"
        elif self._state == RobotLEDState.IDLE:
            return "#00FF00"
        elif self._state in (RobotLEDState.STREAMING, RobotLEDState.TRANSMITTING):
            return "#00FF00" if self._blink_phase else "#003300"
        return "#333333"

    def _draw_robot(self, led_color: str):
        """Draws the futuristic robot vector with dynamic LED eyes and antenna."""
        if not self._canvas:
            return
        self._canvas.delete("all")

        scale = self.size / 56.0
        s = lambda val: val * scale

        # 1. Dark Circular/Pill Background Capsule
        self._canvas.create_oval(
            s(2), s(2), s(54), s(54),
            fill="#121318", outline="#282a36", width=s(2)
        )

        # 2. Antenna Stalk & Antenna LED Dot
        self._canvas.create_line(
            s(28), s(16), s(28), s(9),
            fill="#64748b", width=s(2)
        )
        self._canvas.create_oval(
            s(25), s(6), s(31), s(12),
            fill=led_color, outline="#1e293b", width=s(1)
        )

        # 3. Ears / Side Connectors
        self._canvas.create_rectangle(
            s(11), s(25), s(15), s(35),
            fill="#475569", outline="#334155", width=s(1)
        )
        self._canvas.create_rectangle(
            s(41), s(25), s(45), s(35),
            fill="#475569", outline="#334155", width=s(1)
        )

        # 4. Robot Head Body
        self._canvas.create_rectangle(
            s(15), s(16), s(41), s(44),
            fill="#1e222d", outline="#3b4252", width=s(2)
        )

        # 5. Visor (Dark Screen)
        self._canvas.create_rectangle(
            s(19), s(22), s(37), s(34),
            fill="#090a0f", outline="#2e3440", width=s(1)
        )

        # 6. Robot Eyes (Dynamic Glowing LED)
        self._canvas.create_oval(
            s(21), s(25), s(26), s(31),
            fill=led_color, outline="", width=0
        )
        self._canvas.create_oval(
            s(30), s(25), s(35), s(31),
            fill=led_color, outline="", width=0
        )

        # 7. Speaker / Mouth Grille
        self._canvas.create_line(
            s(22), s(39), s(34), s(39),
            fill="#475569", width=s(1.5)
        )

    def _schedule_blink(self):
        if self._blink_timer_id is not None and self.root:
            try:
                self.root.after_cancel(self._blink_timer_id)
            except Exception:
                pass
            self._blink_timer_id = None

        if self._state in (RobotLEDState.STREAMING, RobotLEDState.TRANSMITTING) and self.root and self._is_running:
            self._blink_timer_id = self.root.after(200, self._step_blink)

    def _step_blink(self):
        if self._state not in (RobotLEDState.STREAMING, RobotLEDState.TRANSMITTING):
            return
        self._blink_phase = not self._blink_phase
        self._draw_robot(self._get_led_color())
        self._schedule_blink()

    def set_state(self, state: RobotLEDState):
        """Sets the LED indicator state thread-safely via root.after()."""
        self._state = state
        if not self.root or not self._is_running:
            return
        self.root.after(0, self._apply_state_change)

    def _apply_state_change(self):
        if self._state not in (RobotLEDState.STREAMING, RobotLEDState.TRANSMITTING):
            if self._blink_timer_id is not None:
                try:
                    self.root.after_cancel(self._blink_timer_id)
                except Exception:
                    pass
                self._blink_timer_id = None
            self._blink_phase = False
            self._draw_robot(self._get_led_color())
        else:
            self._blink_phase = True
            self._draw_robot(self._get_led_color())
            self._schedule_blink()

    def set_idle(self):
        """State 1: SOLID BRIGHT GREEN (#00FF00) - Session connected & idle."""
        self.set_state(RobotLEDState.IDLE)

    def set_streaming(self):
        """State 2: PULSING/BLINKING GREEN (#00FF00 <-> #003300 every 200ms) - Data in flight."""
        self.set_state(RobotLEDState.STREAMING)

    def set_inactive(self):
        """State 3: DIM CHARCOAL (#333333) - Copilot stopped / offline."""
        self.set_state(RobotLEDState.INACTIVE)

    def set_traffic_state(self, is_transmitting: bool):
        """Hook callback: sets blinking green when transmitting, solid green when idle."""
        if is_transmitting:
            self.set_streaming()
        else:
            if self._state not in (RobotLEDState.INACTIVE, RobotLEDState.OFF):
                self.set_idle()

    def show(self):
        """Shows the floating robot widget."""
        if not self._is_running:
            self.start()
        if self._thread is not None and not self._ready_event.is_set():
            self._ready_event.wait(timeout=2.0)
        if self.root:
            self._is_visible = True
            self.root.after(0, lambda: (
                self.root.deiconify(),
                self.root.attributes("-topmost", True),
                self.root.lift(),
                self._apply_window_styles()
            ))

    def hide(self):
        """Hides the floating robot widget."""
        self._is_visible = False
        if self.root:
            self.root.after(0, self.root.withdraw)

    def stop(self):
        """Terminates the robot widget and UI thread cleanly."""
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
