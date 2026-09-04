"""
hud_overlay.py - Cursor-Context Minimal HUD Painter
Windows Edge Module: Frameless, transparent, click-through overlay with cursor anchoring.

Atomic Task 1: Skeleton & HUD Visual Prototype
- Uses PyQt6 with FramelessWindowHint, WindowStaysOnTopHint, Tool.
- WA_TranslucentBackground enabled.
- Fetches cursor coordinates via win32api.GetCursorPos().
- Renders two visual test states on screen:
  1. An emerald-green target circle (radius 30px, line width 3px) around the mouse cursor.
  2. A compact, dark rounded card (rgba(20, 24, 30, 0.85)) offset by +30px, +30px displaying monospace text:
     [COGNITIVE ANCHOR: ONLINE]
     Target: Local Windows Edge
- Automatically closes and cleans up process memory after 3000ms using QTimer.
"""

import sys
import os
import gc
import time
import math
import argparse
import ctypes
from typing import Optional, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Enable per-monitor DPI awareness before Qt / Win32 initializes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import threading
import win32api
import win32gui
import win32con

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, QObject, pyqtSignal
from PyQt6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QBrush,
    QFont,
    QFontMetrics,
    QCursor
)


class SubtitleBridge(QObject):
    """Thread-safe Qt signal bridge dispatching real-time subtitle chunks to the GUI thread."""
    subtitle_signal = pyqtSignal(str)
    hide_signal = pyqtSignal()


_GLOBAL_SUBTITLE_BRIDGE: Optional[SubtitleBridge] = None


def get_subtitle_bridge() -> SubtitleBridge:
    global _GLOBAL_SUBTITLE_BRIDGE
    if _GLOBAL_SUBTITLE_BRIDGE is None:
        _GLOBAL_SUBTITLE_BRIDGE = SubtitleBridge()
    return _GLOBAL_SUBTITLE_BRIDGE


def ensure_interactive_station():
    """
    Attach process and calling thread to the interactive desktop (WinSta0\\Default).
    Prevents Win32 Access Denied (Error 5) when querying cursor coordinates from background contexts.
    """
    try:
        import win32service
        hwinsta = win32service.OpenWindowStation("WinSta0", False, win32con.MAXIMUM_ALLOWED)
        hwinsta.SetProcessWindowStation()
        hdesk = win32service.OpenDesktop("Default", 0, False, win32con.MAXIMUM_ALLOWED)
        hdesk.SetThreadDesktop()
    except Exception:
        pass


def get_current_cursor_pos() -> Tuple[int, int]:
    """
    Retrieves current cursor coordinates (x, y) using win32api.GetCursorPos().
    Includes WinSta0 interactive attachment and graceful fallbacks.
    """
    ensure_interactive_station()

    # 1. Primary: win32api.GetCursorPos()
    try:
        x, y = win32api.GetCursorPos()
        # Sanity check if coordinate is valid desktop coordinate
        if 0 <= x <= 10000 and 0 <= y <= 10000:
            return (int(x), int(y))
    except Exception:
        pass

    # 2. Fallback: QCursor.pos()
    try:
        pos = QCursor.pos()
        if (pos.x() > 0 or pos.y() > 0) and pos.x() <= 10000 and pos.y() <= 10000:
            return (int(pos.x()), int(pos.y()))
    except Exception:
        pass

    # 3. Fallback: Center of primary screen if available
    app = QApplication.instance()
    if app:
        screen = app.primaryScreen()
        if screen:
            geom = screen.geometry()
            return (geom.width() // 2, geom.height() // 2)

    return (400, 300)


def get_default_text_for_mode(mode: str) -> str:
    """Returns canonical status text for each FSM state."""
    m = mode.upper()
    if m == "LISTENING":
        return "[LISTENING] กำลังฟังคำสั่ง...."
    elif m == "THINKING":
        return "[THINKING] กำลังประมวลผล...."
    elif m == "SPEAKING":
        return "[SPEAKING] กำลังตอบกลับ (กด F20 เพื่อขัดจังหวะ)."
    elif m == "STANDBY":
        return ""
    elif m == "ACTION":
        return "[COGNITIVE ANCHOR: ONLINE]\nTarget: Local Windows Edge"
    elif m == "ERROR":
        return "[ERROR: FAILED]"
    return f"[{m}]"


_GLOBAL_HUD_WINDOW: Optional["HUDOverlayWindow"] = None


def hide_overlay():
    """
    Hides the HUD overlay across process and in-process window references.
    Guarantees STANDBY state leaves zero visible HUD elements on screen.
    """
    global _GLOBAL_HUD_WINDOW
    if _GLOBAL_HUD_WINDOW is not None:
        try:
            _GLOBAL_HUD_WINDOW.hide_overlay()
        except Exception:
            pass
    try:
        from terminal_actuator import kill_all_hud_overlays
        kill_all_hud_overlays()
    except Exception:
        pass


def show_response_box(text_chunk: str):
    """
    UI Freeze Directive: Response box pipeline is bypassed/frozen for pure low-latency audio-first core.
    """
    pass


def hide_response_box():
    """
    UI Freeze Directive: Response box pipeline is bypassed/frozen for pure low-latency audio-first core.
    """
    pass


def update_subtitle(chunk: str):
    """
    UI Freeze Directive: Subtitle streaming pipeline is bypassed/frozen for pure low-latency audio-first core.
    """
    pass


class HUDOverlayWindow(QWidget):
    """
    Full-screen, transparent, click-through, always-on-top HUD window.
    Renders cursor-anchored HUD elements without stealing focus or blocking inputs.
    Supports real-time FSM states: STANDBY, LISTENING, THINKING, SPEAKING.
    """

    MODE_STANDBY = "STANDBY"
    MODE_LISTENING = "LISTENING"
    MODE_THINKING = "THINKING"
    MODE_SPEAKING = "SPEAKING"
    MODE_ACTION = "ACTION"
    MODE_ERROR = "ERROR"
    MODE_CONFIRMATION = "CONFIRMATION"

    DEFAULT_BODY_TEXT = (
        "[COGNITIVE ANCHOR: ONLINE]\n"
        "Target: Local Windows Edge"
    )

    def __init__(
        self,
        mode: str = "ACTION",
        text: Optional[str] = None,
        duration: float = 3.0,
        cursor_pos: Optional[Tuple[int, int]] = None,
        action_name: Optional[str] = None,
        command: Optional[str] = None,
        phrase: Optional[str] = None,
        subtitle: Optional[str] = None,
        target_box: Optional[Tuple[float, float, float, float]] = None
    ):
        super().__init__()
        global _GLOBAL_HUD_WINDOW
        _GLOBAL_HUD_WINDOW = self

        self.mode = mode.upper()
        if text is not None:
            self.text = text
        else:
            self.text = get_default_text_for_mode(self.mode)
        self.duration = duration
        self.cursor_pos = cursor_pos if cursor_pos is not None else get_current_cursor_pos()
        self.action_name = action_name or "Run Command"
        self.command = command or ""
        self.phrase = phrase or ""
        self.subtitle = subtitle
        self._subtitle_chunks = self._chunk_thai_text_punchy(subtitle) if subtitle else []
        self._subtitle_start_time = time.perf_counter()
        self.target_box = target_box

        self._key_timer: Optional[QTimer] = None
        self._pulse_timer: Optional[QTimer] = None
        self._is_resolved = False

        self._init_window()
        if self.mode == self.MODE_SPEAKING or self.mode == self.MODE_LISTENING:
            self._start_pulse_timer()

        # Wire thread-safe subtitle bridge signals to GUI thread slots
        bridge = get_subtitle_bridge()
        bridge.subtitle_signal.connect(self.show_response_box)
        bridge.hide_signal.connect(self.hide_response_box)
        self._start_stdin_listener()

    def _start_stdin_listener(self):
        """Listens on stdin for piped realtime subtitle stream if run as child process."""
        def _stdin_reader():
            while not self._is_resolved:
                try:
                    line = sys.stdin.readline()
                    if not line:
                        break
                    line = line.strip()
                    if line.startswith("SUBTITLE:"):
                        txt = line[len("SUBTITLE:"):].strip()
                        get_subtitle_bridge().subtitle_signal.emit(txt)
                    elif line.startswith("HIDE_SUBTITLE"):
                        get_subtitle_bridge().hide_signal.emit()
                    elif line.startswith("HIDE"):
                        QTimer.singleShot(0, self.hide_overlay)
                    elif line.startswith("MODE:"):
                        rest = line[len("MODE:"):].strip()
                        if "|" in rest:
                            new_m, new_txt = rest.split("|", 1)
                        else:
                            new_m, new_txt = rest, None
                        QTimer.singleShot(0, lambda m=new_m, t=new_txt: self.set_state(m, t))
                except Exception:
                    break
        threading.Thread(target=_stdin_reader, daemon=True, name="HUDStdinReader").start()


    def _init_window(self):
        # Configure non-interactive, transparent, topmost window flags
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        # Spanning full desktop / virtual screen geometry
        app = QApplication.instance()
        screen = app.primaryScreen() if app else None
        if screen:
            self.screen_geom = screen.virtualGeometry() if hasattr(screen, "virtualGeometry") else screen.geometry()
        else:
            self.screen_geom = QRectF(0, 0, 1920, 1080).toRect()

        self.setGeometry(self.screen_geom)

        # Ensure cursor position is within visible desktop bounds
        cx, cy = self.cursor_pos
        if cx < self.screen_geom.left() or cx > self.screen_geom.right() or cy < self.screen_geom.top() or cy > self.screen_geom.bottom():
            self.cursor_pos = (
                self.screen_geom.left() + self.screen_geom.width() // 2,
                self.screen_geom.top() + self.screen_geom.height() // 2
            )

        # In CONFIRMATION mode, start non-blocking global key poller (Y / N / A)
        if self.mode == self.MODE_CONFIRMATION:
            # Prime Win32 key states
            win32api.GetAsyncKeyState(0x59)  # Y
            win32api.GetAsyncKeyState(0x4E)  # N
            win32api.GetAsyncKeyState(0x41)  # A
            win32api.GetAsyncKeyState(0x1B)  # Esc

            self._key_timer = QTimer(self)
            self._key_timer.timeout.connect(self._poll_confirmation_keys)
            self._key_timer.start(25)  # 25ms interval

        # Auto-close timer
        if self.duration > 0:
            QTimer.singleShot(int(self.duration * 1000), self.close_and_exit)

    def _poll_confirmation_keys(self):
        """Polls for global user verification keystrokes: [Y] Yes, [N] No, [A] Always Learn."""
        if self._is_resolved or self.mode != self.MODE_CONFIRMATION:
            return

        # Key 'Y' (0x59) -> Confirm & run once
        if win32api.GetAsyncKeyState(0x59) & 0x8000:
            self._is_resolved = True
            if self._key_timer:
                self._key_timer.stop()
            self._on_user_confirm(save_always=False)
            return

        # Key 'A' (0x41) -> Confirm, learn into user_rules.json, & run
        if win32api.GetAsyncKeyState(0x41) & 0x8000:
            self._is_resolved = True
            if self._key_timer:
                self._key_timer.stop()
            self._on_user_confirm(save_always=True)
            return

        # Key 'N' (0x4E) or Key 'Esc' (0x1B) -> Cancel execution
        if (win32api.GetAsyncKeyState(0x4E) & 0x8000) or (win32api.GetAsyncKeyState(0x1B) & 0x8000):
            self._is_resolved = True
            if self._key_timer:
                self._key_timer.stop()
            self._on_user_cancel()
            return

    def _on_user_confirm(self, save_always: bool = False):
        """Executes the command, optionally saves the rule, and transitions HUD to ACTION mode."""
        if save_always and self.phrase and self.command:
            try:
                from intent_memory import save_rule
                save_rule(
                    phrase=self.phrase,
                    command=self.command,
                    auto_submit=True,
                    action_name=self.action_name
                )
            except Exception as e:
                sys.stderr.write(f"[HUD Confirmation] Error saving rule: {e}\n")

        # Execute terminal command
        cmd_to_run = self.command if self.command else "echo Confirmation Received"
        try:
            from terminal_actuator import TerminalActuator
            actuator = TerminalActuator(default_hud_duration=2.5)
            res = actuator.execute_sync(cmd_to_run)
            exit_code = res.exit_code
            first_line = res.stdout.strip().splitlines()[0] if res.stdout.strip() else (res.stderr.strip().splitlines()[0] if res.stderr.strip() else res.command)
        except Exception as e:
            exit_code = 1
            first_line = str(e)

        # Transition to emerald (ACTION) or red (ERROR)
        self.mode = self.MODE_ACTION if exit_code == 0 else self.MODE_ERROR
        tag = "[LEARNED & EXECUTED]" if save_always else "[CONFIRMED & EXECUTED]"
        self.text = f"{tag} Exit {exit_code}\n> {cmd_to_run}\n{first_line}"
        self.update()

        QTimer.singleShot(2500, self.close_and_exit)

    def _on_user_cancel(self):
        """Cancels execution and transitions HUD to cancelled state briefly."""
        self.mode = self.MODE_ERROR
        self.text = f"[CANCELLED]\nยกเลิกคำสั่งเรียบร้อย\n> {self.command}"
        self.update()
        QTimer.singleShot(1500, self.close_and_exit)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_native_win32_styles()
        self._force_topmost()

    def _force_topmost(self):
        """Forces the window to stay strictly Topmost using native Win32 SetWindowPos."""
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                win32gui.SetWindowPos(
                    hwnd,
                    win32con.HWND_TOPMOST,
                    0, 0, 0, 0,
                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW
                )
            except Exception as e:
                sys.stderr.write(f"[HUD] Warning forcing topmost: {e}\n")

    def _apply_native_win32_styles(self):
        """
        Enforce WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, and WS_EX_NOACTIVATE
        to guarantee zero input blocking and prevent OS focus stealing.
        """
        try:
            hwnd = int(self.winId())
            GWL_EXSTYLE = win32con.GWL_EXSTYLE
            ex_style = win32gui.GetWindowLong(hwnd, GWL_EXSTYLE)
            new_style = (
                ex_style
                | win32con.WS_EX_TRANSPARENT
                | win32con.WS_EX_LAYERED
                | win32con.WS_EX_TOOLWINDOW
                | win32con.WS_EX_TOPMOST
                | win32con.WS_EX_NOACTIVATE
            )
            win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, new_style)
            self._force_topmost()
        except Exception as e:
            sys.stderr.write(f"[HUD] Warning applying Win32 styles: {e}\n")

    def _start_pulse_timer(self):
        if self._pulse_timer is None:
            self._pulse_timer = QTimer(self)
            self._pulse_timer.timeout.connect(self._on_pulse_tick)
            self._pulse_timer.start(33)  # ~30 FPS

    def _stop_pulse_timer(self):
        if self._pulse_timer is not None:
            self._pulse_timer.stop()
            self._pulse_timer = None

    def _on_pulse_tick(self):
        if (self.mode in (self.MODE_SPEAKING, self.MODE_LISTENING) or self.target_box is not None) and self.isVisible():
            self.update()

    def _chunk_thai_text_punchy(self, text: str) -> list:
        """
        Splits incoming Thai text into punchy, short phrases (2-4 words per burst)
        centered on screen rather than continuous ticker or slow typewriter lines.
        """
        if not text:
            return []
        clean = text.strip()
        import re
        words = [w.strip() for w in re.split(r'(\s+)', clean) if w.strip()]
        if len(words) >= 2:
            chunks = []
            curr = []
            for w in words:
                curr.append(w)
                if len(curr) >= 3 or len(" ".join(curr)) >= 20:
                    chunks.append(" ".join(curr))
                    curr = []
            if curr:
                chunks.append(" ".join(curr))
            return chunks

        # Continuous Thai text without spaces: segment every 14-18 chars (approx 2-4 Thai words)
        chunks = []
        step = 16
        for i in range(0, len(clean), step):
            part = clean[i:i + step].strip()
            if part:
                chunks.append(part)
        return chunks if chunks else [clean]

    def set_state(
        self,
        mode: str,
        text: Optional[str] = None,
        cursor_pos: Optional[Tuple[int, int]] = None,
        subtitle: Optional[str] = None,
        target_box: Optional[Tuple[float, float, float, float]] = None
    ):
        """
        Dynamically updates the HUD visual state and text in real-time without spawning new processes.
        """
        self.mode = mode.upper()
        if self.mode == self.MODE_STANDBY:
            self.subtitle = None
            self._subtitle_chunks = []
            self.target_box = None
            self.hide_overlay()
            return

        if text is not None:
            self.text = text
        else:
            self.text = get_default_text_for_mode(self.mode)

        if cursor_pos is not None:
            self.cursor_pos = cursor_pos
        else:
            self.cursor_pos = get_current_cursor_pos()

        self.subtitle = subtitle
        self._subtitle_chunks = self._chunk_thai_text_punchy(subtitle) if subtitle else []
        self._subtitle_start_time = time.perf_counter()
        self.target_box = target_box

        if self.mode in (self.MODE_SPEAKING, self.MODE_LISTENING) or self.target_box is not None:
            self._start_pulse_timer()
        else:
            self._stop_pulse_timer()

        self.show()
        self.update()

    def show_response_box(self, text_chunk: str):
        """
        Explicitly forces the center response box to pop up in the lower-center of the screen.
        Updates subtitle chunks, enforces Topmost z-order, and forces immediate GUI repaint.
        """
        if not text_chunk:
            return
        self.subtitle = text_chunk
        self._subtitle_chunks = self._chunk_thai_text_punchy(text_chunk)
        self._subtitle_start_time = time.perf_counter()
        if self.mode != self.MODE_SPEAKING:
            self.mode = self.MODE_SPEAKING
        if not self.isVisible():
            self.show()
        self._force_topmost()
        self._start_pulse_timer()
        self.update()
        self.repaint()
        app = QApplication.instance()
        if app:
            app.processEvents()

    def hide_response_box(self):
        """Hides the center response box and forces GUI repaint."""
        self.subtitle = None
        self._subtitle_chunks = []
        self.update()
        self.repaint()
        app = QApplication.instance()
        if app:
            app.processEvents()

    def hide_overlay(self):
        """Hides the HUD overlay window immediately."""
        self._stop_pulse_timer()
        self.subtitle = None
        self._subtitle_chunks = []
        self.target_box = None
        self.hide()

    def close_and_exit(self):
        """Clean up window, quit Qt event loop, and free process memory."""
        self._stop_pulse_timer()
        self.close()
        app = QApplication.instance()
        if app:
            app.quit()
        gc.collect()

    def paintEvent(self, event):
        """Renders HUD graphics: docked status HUD, bottom subtitle box, and visual target reticle."""
        if self.mode == self.MODE_STANDBY:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # 1. Visual Grounding Highlight Box (over mapped window coordinates while speaking)
        if self.target_box:
            self._draw_target_highlight_box(painter)

        # 2. Docked Status HUD (pinned permanently to bottom-right corner of active screen)
        self._draw_docked_status_hud(painter)

        # 3. Bottom Response Subtitle Box (centered at lower edge during SPEAKING or whenever subtitle is present)
        if self.subtitle and bool(self.subtitle.strip()):
            self._draw_bottom_subtitle_box(painter)

        # 4. Cursor Feedback Elements
        cx = float(self.cursor_pos[0] - self.screen_geom.x())
        cy = float(self.cursor_pos[1] - self.screen_geom.y())

        if self.mode == self.MODE_CONFIRMATION:
            self._draw_amber_target_circle(painter, cx, cy)
            self._draw_confirmation_card(painter, cx, cy, offset=25)
        elif self.mode == self.MODE_ACTION:
            self._draw_emerald_target_circle(painter, cx, cy)
            self._draw_cognitive_anchor_card(painter, cx, cy, offset=30)
        elif self.mode == self.MODE_ERROR:
            self._draw_red_target_circle(painter, cx, cy)
            self._draw_cognitive_anchor_card(painter, cx, cy, offset=30)

        painter.end()

    def _draw_docked_status_hud(self, painter: QPainter):
        """
        Pins the Status HUD (STANDBY, LISTENING, THINKING, SPEAKING)
        permanently at the bottom-right corner of the active screen.
        """
        app = QApplication.instance()
        screen = app.primaryScreen() if app else None
        avail = screen.availableGeometry() if screen else self.screen_geom

        rel_left = avail.left() - self.screen_geom.left()
        rel_top = avail.top() - self.screen_geom.top()
        rel_w = avail.width()
        rel_h = avail.height()

        hud_w = 340
        hud_h = 58
        margin_r = 24
        margin_b = 20

        hx = rel_left + rel_w - hud_w - margin_r
        hy = rel_top + rel_h - hud_h - margin_b
        hud_rect = QRectF(hx, hy, hud_w, hud_h)

        t = time.perf_counter()
        pulse = 0.5 + 0.5 * math.sin(t * 7.5)

        if self.mode == self.MODE_LISTENING:
            accent_color = QColor(6, 182, 212)      # Electric Cyan
            accent_glow = QColor(6, 182, 212, int(45 + pulse * 45))
            status_title = "[ LISTENING ]"
            status_desc = "กำลังฟังเสียง... (กด F20 อีกครั้งเพื่อส่ง)"
        elif self.mode == self.MODE_THINKING:
            accent_color = QColor(245, 158, 11)    # Amber
            accent_glow = QColor(245, 158, 11, int(40 + pulse * 35))
            status_title = "[ THINKING ]"
            status_desc = "กำลังประมวลผลคำสั่ง..."
        elif self.mode == self.MODE_SPEAKING:
            accent_color = QColor(16, 185, 129)    # Emerald Mint
            accent_glow = QColor(16, 185, 129, int(50 + pulse * 55))
            status_title = "[ SPEAKING ]"
            status_desc = "กำลังตอบกลับ (กด F20 เพื่อหยุด)"
        elif self.mode == self.MODE_ERROR:
            accent_color = QColor(239, 68, 68)     # Crimson Red
            accent_glow = QColor(239, 68, 68, 50)
            status_title = "[ ERROR ]"
            status_desc = "เกิดข้อผิดพลาดในการประมวลผล"
        else:
            accent_color = QColor(16, 185, 129)
            accent_glow = QColor(16, 185, 129, 40)
            status_title = f"[ {self.mode} ]"
            status_desc = "Cognitive Anchor Online"

        # 1. Outer Glow
        glow_pen = QPen(accent_glow, 4.5)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(hud_rect, 14.0, 14.0)

        # 2. Card Body: Frosted Deep Slate
        card_bg = QColor(15, 23, 42, 238)
        border_pen = QPen(accent_color, 1.5)
        painter.setPen(border_pen)
        painter.setBrush(QBrush(card_bg))
        painter.drawRoundedRect(hud_rect, 14.0, 14.0)

        # 3. Status Indicator Ring / Orb on left
        orb_cx = hx + 28
        orb_cy = hy + (hud_h / 2)
        orb_r = 9.0 + (pulse * 2.5 if self.mode in (self.MODE_LISTENING, self.MODE_SPEAKING) else 0)

        painter.setPen(QPen(accent_color, 2.0))
        painter.setBrush(QBrush(QColor(accent_color.red(), accent_color.green(), accent_color.blue(), 160)))
        painter.drawEllipse(QPointF(orb_cx, orb_cy), orb_r, orb_r)

        # Center pip
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 240)))
        painter.drawEllipse(QPointF(orb_cx, orb_cy), 2.8, 2.8)

        # 4. Text Content (Title & Description)
        text_x = hx + 50
        text_w = hud_w - 60

        title_font = QFont("Consolas", 10, QFont.Weight.Bold)
        title_font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(title_font)
        painter.setPen(accent_color)
        painter.drawText(QRectF(text_x, hy + 10, text_w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, status_title)

        desc_font = QFont("Segoe UI", 9, QFont.Weight.Medium)
        painter.setFont(desc_font)
        painter.setPen(QColor(203, 213, 225))
        painter.drawText(QRectF(text_x, hy + 28, text_w, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, status_desc)

    def _draw_bottom_subtitle_box(self, painter: QPainter):
        """
        Transparent TikTok-Style Response Box:
        - Located at the bottom-center of the screen with a subtle dark translucent backing (alpha ~ 0.75).
        - Displays incoming Thai text in punchy, short chunked phrases (2-4 words per burst)
          centered on screen rather than continuous ticker or slow typewriter lines.
        - Uses large, high-contrast bold typography with bright active highlighting (Neon Yellow #FFE600)
          on the currently spoken phrase to command visual attention.
        - Disappears immediately upon double-click dismiss or when user initiates a new turn (F20).
        """
        if not self.subtitle or not self.subtitle.strip():
            return

        chunks = getattr(self, "_subtitle_chunks", [])
        if not chunks:
            chunks = self._chunk_thai_text_punchy(self.subtitle)
            self._subtitle_chunks = chunks

        if not chunks:
            return

        app = QApplication.instance()
        screen = app.primaryScreen() if app else None
        avail = screen.availableGeometry() if screen else self.screen_geom

        rel_left = avail.left() - self.screen_geom.left()
        rel_top = avail.top() - self.screen_geom.top()
        rel_w = avail.width()
        rel_h = avail.height()

        # Time-based burst chunk progression (~450ms per burst)
        start_t = getattr(self, "_subtitle_start_time", time.perf_counter())
        elapsed = time.perf_counter() - start_t
        chunk_idx = min(len(chunks) - 1, max(0, int(elapsed / 0.45)))
        active_phrase = chunks[chunk_idx]

        # Centered TikTok-Style Box geometry at bottom-center
        box_w = min(880, max(520, int(rel_w * 0.58)))
        box_h = 104
        box_x = rel_left + (rel_w - box_w) / 2
        box_y = rel_top + rel_h - box_h - 26
        box_rect = QRectF(box_x, box_y, box_w, box_h)

        # 1. Atmospheric Soft Glow
        glow_pen = QPen(QColor(16, 185, 129, 45), 5.0)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(box_rect, 16.0, 16.0)

        # 2. Subtle Dark Translucent Backing (alpha ~ 0.75: 192 / 255 = 0.753)
        card_bg = QColor(10, 15, 26, 192)
        border_pen = QPen(QColor(16, 185, 129, 180), 1.5)
        painter.setPen(border_pen)
        painter.setBrush(QBrush(card_bg))
        painter.drawRoundedRect(box_rect, 16.0, 16.0)

        # 3. Header Badge: 🎙️ GEMINI LIVE
        badge_font = QFont("Consolas", 9, QFont.Weight.Bold)
        painter.setFont(badge_font)
        painter.setPen(QColor(52, 211, 153))
        painter.drawText(
            QRectF(box_x, box_y + 8, box_w, 16),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "🎙️ GEMINI LIVE"
        )

        # 4. Large, High-Contrast Bold Typography with Neon Yellow (#FFE600) Active Highlighting
        punchy_font = QFont("Segoe UI", 21, QFont.Weight.Bold)
        punchy_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        painter.setFont(punchy_font)

        # Draw active punchy phrase centered on screen in Neon Yellow
        neon_yellow = QColor(255, 230, 0)
        painter.setPen(neon_yellow)
        content_rect = QRectF(box_x + 16, box_y + 26, box_w - 32, box_h - 32)
        painter.drawText(
            content_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap,
            active_phrase
        )

    def _draw_target_highlight_box(self, painter: QPainter):
        """
        Draws a transient neon highlight box on screen over the mapped window coordinates while speaking.
        """
        if not self.target_box:
            return

        bx, by, bw, bh = self.target_box
        rx = float(bx - self.screen_geom.x())
        ry = float(by - self.screen_geom.y())
        box_rect = QRectF(rx, ry, bw, bh)

        t = time.perf_counter()
        pulse = 0.5 + 0.5 * math.sin(t * 8.0)
        neon_cyan = QColor(6, 182, 212)
        bracket_cyan = QColor(34, 211, 238)

        # 1. Soft outer glow & semi-transparent tinted fill
        glow_alpha = int(35 + pulse * 45)
        glow_pen = QPen(QColor(6, 182, 212, glow_alpha), 6.0)
        painter.setPen(glow_pen)
        painter.setBrush(QBrush(QColor(6, 182, 212, 18)))
        painter.drawRect(box_rect)

        # 2. Main neon border
        main_pen = QPen(neon_cyan, 2.0)
        painter.setPen(main_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(box_rect)

        # 3. Corner L-brackets (Futuristic Reticle)
        bracket_len = min(22.0, max(8.0, bw / 3), max(8.0, bh / 3))
        bracket_pen = QPen(bracket_cyan, 3.5)
        painter.setPen(bracket_pen)

        # Top-Left
        painter.drawLine(QPointF(rx, ry), QPointF(rx + bracket_len, ry))
        painter.drawLine(QPointF(rx, ry), QPointF(rx, ry + bracket_len))

        # Top-Right
        painter.drawLine(QPointF(rx + bw, ry), QPointF(rx + bw - bracket_len, ry))
        painter.drawLine(QPointF(rx + bw, ry), QPointF(rx + bw, ry + bracket_len))

        # Bottom-Left
        painter.drawLine(QPointF(rx, ry + bh), QPointF(rx + bracket_len, ry + bh))
        painter.drawLine(QPointF(rx, ry + bh), QPointF(rx, ry + bh - bracket_len))

        # Bottom-Right
        painter.drawLine(QPointF(rx + bw, ry + bh), QPointF(rx + bw - bracket_len, ry + bh))
        painter.drawLine(QPointF(rx + bw, ry + bh), QPointF(rx + bw, ry + bh - bracket_len))

        # 4. Floating Tag: [ TARGET FOCUS ]
        tag_w = 96
        tag_h = 20
        tag_rect = QRectF(rx, max(4.0, ry - tag_h - 4), tag_w, tag_h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(6, 182, 212, 230)))
        painter.drawRoundedRect(tag_rect, 4.0, 4.0)

        tag_font = QFont("Consolas", 8, QFont.Weight.Bold)
        painter.setFont(tag_font)
        painter.setPen(QColor(15, 23, 42))
        painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, "TARGET FOCUS")

    def _draw_emerald_pulsing_circle(self, painter: QPainter, cx: float, cy: float):
        """
        Visual indicator for SPEAKING mode:
        Emerald Pulsing Ring with precision reticle ticks around mouse cursor.
        Rhythmically pulses to indicate real-time audio playback and F20 barge-in interruption readiness.
        """
        import math
        t = time.perf_counter()
        # Smooth pulsing oscillation (approx 1.2 Hz)
        pulse = 0.5 + 0.5 * math.sin(t * 7.5)
        pulse_r = 30.0 + (pulse * 5.5)  # 30.0 to 35.5 px
        glow_alpha = int(40 + pulse * 65)  # 40 to 105 alpha

        # Dynamic pulsing emerald glow
        glow_pen = QPen(QColor(16, 185, 129, glow_alpha), 6)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), pulse_r, pulse_r)

        # Primary emerald target circle
        emerald_color = QColor(16, 185, 129)  # #10b981
        main_pen = QPen(emerald_color, 3.0)
        painter.setPen(main_pen)
        painter.drawEllipse(QPointF(cx, cy), 30.0, 30.0)

        # Precision reticle ticks (North, South, East, West)
        reticle_pen = QPen(QColor(52, 211, 153, 230), 1.5)
        painter.setPen(reticle_pen)
        painter.drawLine(QPointF(cx, cy - 22), QPointF(cx, cy - 35))
        painter.drawLine(QPointF(cx, cy + 22), QPointF(cx, cy + 35))
        painter.drawLine(QPointF(cx - 35, cy), QPointF(cx - 22, cy))
        painter.drawLine(QPointF(cx + 22, cy), QPointF(cx + 35, cy))

        # Precision center anchor pip (radius 2.5px)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(16, 185, 129, 240)))
        painter.drawEllipse(QPointF(cx, cy), 2.5, 2.5)

    def _draw_cyan_listening_reticle(self, painter: QPainter, cx: float, cy: float):
        """Visual indicator for LISTENING mode: Glowing Cyan target circle and cardinal crosshairs."""
        cyan_color = QColor(6, 182, 212)  # Electric Cyan: #06b6d4
        glow_pen = QPen(QColor(6, 182, 212, 55), 6)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), 34.0, 34.0)

        main_pen = QPen(cyan_color, 3.0)
        painter.setPen(main_pen)
        painter.drawEllipse(QPointF(cx, cy), 30.0, 30.0)

        reticle_pen = QPen(QColor(34, 211, 238, 230), 1.5)
        painter.setPen(reticle_pen)
        painter.drawLine(QPointF(cx, cy - 22), QPointF(cx, cy - 35))
        painter.drawLine(QPointF(cx, cy + 22), QPointF(cx, cy + 35))
        painter.drawLine(QPointF(cx - 35, cy), QPointF(cx - 22, cy))
        painter.drawLine(QPointF(cx + 22, cy), QPointF(cx + 35, cy))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(6, 182, 212, 240)))
        painter.drawEllipse(QPointF(cx, cy), 2.5, 2.5)

    def _draw_thinking_circle(self, painter: QPainter, cx: float, cy: float):
        """
        Visual indicator for THINKING mode: Subtle cyan cognitive pulse ring and reticle.
        """
        cyan_glow = QColor(56, 189, 248, 60)
        cyan_ring = QColor(56, 189, 248, 200)

        # Outer soft glow
        painter.setPen(QPen(cyan_glow, 4.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), 24.0, 24.0)

        # Inner ring
        painter.setPen(QPen(cyan_ring, 2.0))
        painter.drawEllipse(QPointF(cx, cy), 20.0, 20.0)

        # Center dot
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(56, 189, 248, 230)))
        painter.drawEllipse(QPointF(cx, cy), 2.5, 2.5)

    def _draw_emerald_target_circle(self, painter: QPainter, cx: float, cy: float):
        """
        Visual Test State 1:
        Emerald-green target circle (radius 30px, line width 3px) around mouse cursor.
        """
        # Emerald Green: RGB(16, 185, 129) / #10b981
        emerald_color = QColor(16, 185, 129)

        # Subtle outer atmospheric glow
        glow_pen = QPen(QColor(16, 185, 129, 45), 6)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), 34, 34)

        # Primary emerald-green target circle (radius 30px, line width 3px)
        main_pen = QPen(emerald_color, 3.0)
        painter.setPen(main_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), 30.0, 30.0)

        # Precision reticle ticks (North, South, East, West)
        reticle_pen = QPen(QColor(52, 211, 153, 220), 1.5)
        painter.setPen(reticle_pen)
        painter.drawLine(QPointF(cx, cy - 22), QPointF(cx, cy - 35))
        painter.drawLine(QPointF(cx, cy + 22), QPointF(cx, cy + 35))
        painter.drawLine(QPointF(cx - 35, cy), QPointF(cx - 22, cy))
        painter.drawLine(QPointF(cx + 22, cy), QPointF(cx + 35, cy))

        # Precision center anchor pip (radius 2px)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(16, 185, 129, 240)))
        painter.drawEllipse(QPointF(cx, cy), 2.0, 2.0)

    def _draw_red_target_circle(self, painter: QPainter, cx: float, cy: float):
        """
        Visual Error State:
        Crimson-red target circle (radius 30px, line width 3px) around mouse cursor.
        """
        red_color = QColor(239, 68, 68)  # Crimson red: #ef4444

        # Atmospheric red glow
        glow_pen = QPen(QColor(239, 68, 68, 50), 6)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), 34, 34)

        # Primary red target circle (radius 30px, line width 3px)
        main_pen = QPen(red_color, 3.0)
        painter.setPen(main_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), 30.0, 30.0)

        # Precision reticle ticks (North, South, East, West)
        reticle_pen = QPen(QColor(248, 113, 113, 230), 1.5)
        painter.setPen(reticle_pen)
        painter.drawLine(QPointF(cx, cy - 22), QPointF(cx, cy - 35))
        painter.drawLine(QPointF(cx, cy + 22), QPointF(cx, cy + 35))
        painter.drawLine(QPointF(cx - 35, cy), QPointF(cx - 22, cy))
        painter.drawLine(QPointF(cx + 22, cy), QPointF(cx + 35, cy))

        # Precision center anchor pip (radius 2px)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(239, 68, 68, 240)))
        painter.drawEllipse(QPointF(cx, cy), 2.0, 2.0)

    def _draw_amber_target_circle(self, painter: QPainter, cx: float, cy: float):
        """Visual indicator for CONFIRMATION mode: Amber/Gold target circle (r=30px)."""
        amber_color = QColor(245, 158, 11)  # #f59e0b

        # Outer soft glow
        glow_pen = QPen(QColor(245, 158, 11, 45), 6)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), 34, 34)

        # Primary amber target circle (radius 30px, line width 3px)
        main_pen = QPen(amber_color, 3.0)
        painter.setPen(main_pen)
        painter.drawEllipse(QPointF(cx, cy), 30.0, 30.0)

        # Precision reticle ticks
        reticle_pen = QPen(QColor(251, 191, 36, 230), 1.5)
        painter.setPen(reticle_pen)
        painter.drawLine(QPointF(cx, cy - 22), QPointF(cx, cy - 35))
        painter.drawLine(QPointF(cx, cy + 22), QPointF(cx, cy + 35))
        painter.drawLine(QPointF(cx - 35, cy), QPointF(cx - 22, cy))
        painter.drawLine(QPointF(cx + 22, cy), QPointF(cx + 35, cy))

        # Precision center anchor pip
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(245, 158, 11, 240)))
        painter.drawEllipse(QPointF(cx, cy), 2.0, 2.0)

    def _draw_confirmation_card(self, painter: QPainter, cx: float, cy: float, offset: int = 25):
        """Interactive floating prompt beside cursor."""
        font = QFont("Consolas", 10, QFont.Weight.Medium)
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)
        fm = QFontMetrics(font)

        lines = [
            "[VERIFICATION REQUIRED]",
            f"เข้าใจว่า: {self.action_name}",
            f"คำสั่ง:    {self.command}",
            "--------------------------------------------------",
            "[Y] ใช่ (รันครั้งนี้)    |    [N] ไม่ใช่ (ยกเลิก)",
            "[A] จำไว้เสมอ (Auto-Run ในครั้งถัดไปโดยไม่ถามซ้ำ)"
        ]

        pad_x = 16
        pad_y = 14
        line_spacing = 5
        line_h = fm.height()

        max_text_w = max(fm.horizontalAdvance(line) for line in lines)
        card_w = max_text_w + (pad_x * 2)
        card_h = (len(lines) * line_h) + ((len(lines) - 1) * line_spacing) + (pad_y * 2)

        card_x = cx + offset
        card_y = cy + offset

        if card_x + card_w > self.screen_geom.width() - 15:
            card_x = cx - card_w - offset
        if card_y + card_h > self.screen_geom.height() - 15:
            card_y = cy - card_h - offset

        card_rect = QRectF(card_x, card_y, card_w, card_h)

        # Amber glowing border & dark slate body
        painter.setPen(QPen(QColor(245, 158, 11, 220), 1.5))
        painter.setBrush(QBrush(QColor(18, 22, 28, 240)))
        painter.drawRoundedRect(card_rect, 10.0, 10.0)

        curr_y = card_y + pad_y + line_h - 2
        for line in lines:
            line_rect = QRectF(card_x + pad_x, curr_y - line_h, max_text_w, line_h + 2)
            if "[VERIFICATION REQUIRED]" in line:
                painter.setPen(QColor(251, 191, 36))
            elif line.startswith("เข้าใจว่า"):
                painter.setPen(QColor(248, 250, 252))
            elif line.startswith("คำสั่ง"):
                painter.setPen(QColor(56, 189, 248))
            elif line.startswith("---"):
                painter.setPen(QColor(71, 85, 105))
            elif "[Y]" in line:
                painter.setPen(QColor(52, 211, 153))
            elif "[A]" in line:
                painter.setPen(QColor(251, 191, 36))
            else:
                painter.setPen(QColor(203, 213, 225))

            painter.drawText(line_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
            curr_y += line_h + line_spacing

    def _draw_cognitive_anchor_card(self, painter: QPainter, cx: float, cy: float, offset: int = 30):
        """
        Compact dark rounded card (rgba(20, 24, 30, 0.85)) offset from cursor.
        """
        font = QFont("Consolas", 10, QFont.Weight.Medium)
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)
        fm = QFontMetrics(font)

        raw_lines = self.text.split("\n") if self.text else ["[COGNITIVE ANCHOR: ONLINE]", "Target: Local Windows Edge"]
        lines = [line.strip() for line in raw_lines if line.strip()]
        if not lines:
            lines = ["[COGNITIVE ANCHOR: ONLINE]", "Target: Local Windows Edge"]

        pad_x = 14
        pad_y = 12
        line_spacing = 4
        line_h = fm.height()

        max_text_w = max(fm.horizontalAdvance(line) for line in lines)
        card_w = max_text_w + (pad_x * 2)
        card_h = (len(lines) * line_h) + ((len(lines) - 1) * line_spacing) + (pad_y * 2)

        # Offset by +offset, +offset from cursor coordinates
        card_x = cx + offset
        card_y = cy + offset

        # Boundary checks: Flip position if card exceeds screen borders
        if card_x + card_w > self.screen_geom.width() - 15:
            card_x = cx - card_w - offset
        if card_y + card_h > self.screen_geom.height() - 15:
            card_y = cy - card_h - offset

        card_x = max(10, card_x)
        card_y = max(10, card_y)

        card_rect = QRectF(card_x, card_y, card_w, card_h)

        # Card Background: rgba(20, 24, 30, 0.85) -> alpha = int(255 * 0.85) = 216
        card_bg = QColor(20, 24, 30, int(255 * 0.85))
        if self.mode in (self.MODE_THINKING, self.MODE_CONFIRMATION):
            card_border = QColor(245, 158, 11, 190)  # Amber accent border
            glow_color = QColor(245, 158, 11, 40)
        elif self.mode == self.MODE_LISTENING:
            card_border = QColor(6, 182, 212, 190)   # Cyan accent border
            glow_color = QColor(6, 182, 212, 40)
        elif self.mode == self.MODE_ERROR:
            card_border = QColor(239, 68, 68, 190)   # Red error accent border
            glow_color = QColor(239, 68, 68, 45)
        else:
            # SPEAKING / ACTION
            card_border = QColor(16, 185, 129, 180)  # Emerald accent border
            glow_color = QColor(16, 185, 129, 40)

        # 1. Subtle card drop glow
        glow_pen = QPen(glow_color, 4)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(card_rect, 8.0, 8.0)

        # 2. Card body
        painter.setPen(QPen(card_border, 1.2))
        painter.setBrush(QBrush(card_bg))
        painter.drawRoundedRect(card_rect, 8.0, 8.0)

        # 3. Monospace text rendering
        curr_y = card_y + pad_y
        for i, line in enumerate(lines):
            line_rect = QRectF(card_x + pad_x, curr_y, card_w - (pad_x * 2), line_h)

            if "[LISTENING]" in line.upper():
                painter.setPen(QColor(6, 182, 212))  # Electric Cyan
                bold_font = QFont(font)
                bold_font.setWeight(QFont.Weight.Bold)
                painter.setFont(bold_font)
            elif "[THINKING]" in line.upper():
                painter.setPen(QColor(251, 191, 36))  # Amber
                bold_font = QFont(font)
                bold_font.setWeight(QFont.Weight.Bold)
                painter.setFont(bold_font)
            elif "[SPEAKING]" in line.upper():
                painter.setPen(QColor(52, 211, 153))  # Emerald Mint
                bold_font = QFont(font)
                bold_font.setWeight(QFont.Weight.Bold)
                painter.setFont(bold_font)
            elif "[COGNITIVE ANCHOR" in line.upper():
                painter.setPen(QColor(52, 211, 153))  # Emerald/Mint bright
                bold_font = QFont(font)
                bold_font.setWeight(QFont.Weight.Bold)
                painter.setFont(bold_font)
            elif "TARGET:" in line.upper():
                painter.setPen(QColor(226, 232, 240))  # Slate 200 high contrast
                painter.setFont(font)
            elif line.startswith(">") or line.startswith("$"):
                painter.setPen(QColor(250, 204, 21))   # Command yellow
                painter.setFont(font)
            elif "ERR" in line.upper() or "FAIL" in line.upper():
                painter.setPen(QColor(248, 113, 113))  # Red
                painter.setFont(font)
            elif "EXIT 0" in line.upper() or "SUCCESS" in line.upper() or "OK" in line.upper():
                painter.setPen(QColor(52, 211, 153))  # Emerald/Mint
                painter.setFont(font)
            else:
                painter.setPen(QColor(203, 213, 225))  # Slate 300
                painter.setFont(font)

            painter.drawText(line_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
            curr_y += line_h + line_spacing


def show_hud(
    mode: str = "ACTION",
    text: Optional[str] = None,
    duration: float = 3.0,
    cursor_pos: Optional[Tuple[int, int]] = None,
    action_name: Optional[str] = None,
    command: Optional[str] = None,
    phrase: Optional[str] = None,
    subtitle: Optional[str] = None,
    target_box: Optional[Tuple[float, float, float, float]] = None
) -> int:
    """
    Spawns and executes the HUD overlay window inside the Qt application loop.
    Returns the Qt exit code.
    """
    app = QApplication.instance()
    should_exec = False
    if not app:
        app = QApplication(sys.argv)
        should_exec = True

    window = HUDOverlayWindow(
        mode=mode,
        text=text,
        duration=duration,
        cursor_pos=cursor_pos,
        action_name=action_name,
        command=command,
        phrase=phrase,
        subtitle=subtitle,
        target_box=target_box
    )
    window.show()

    if should_exec:
        return app.exec()
    return 0


def main():
    parser = argparse.ArgumentParser(description="Windows Edge - Skeleton & HUD Visual Prototype")
    parser.add_argument(
        "--mode",
        choices=["STANDBY", "LISTENING", "THINKING", "SPEAKING", "ACTION", "ERROR", "CONFIRMATION"],
        default="ACTION",
        help="Visual display mode"
    )
    parser.add_argument("--text", type=str, default=None, help="Monospace body text / payload")
    parser.add_argument("--duration", type=float, default=3.0, help="Auto-close duration in seconds (default: 3.0s / 3000ms)")
    parser.add_argument("--x", type=int, default=None, help="Custom cursor X coordinate")
    parser.add_argument("--y", type=int, default=None, help="Custom cursor Y coordinate")
    parser.add_argument("--action-name", type=str, default=None, help="Action name for confirmation")
    parser.add_argument("--command", type=str, default=None, help="Command to execute upon confirmation")
    parser.add_argument("--phrase", type=str, default=None, help="Spoken phrase to remember")
    parser.add_argument("--subtitle", type=str, default=None, help="Bottom response subtitle text during SPEAKING")
    parser.add_argument("--target-box", type=str, default=None, help="Target bounding box 'x,y,w,h'")
    parser.add_argument("--test-mode", action="store_true", help="Quick self-test validation mode (0.3s duration)")
    parser.add_argument("--preview", action="store_true", help="Force subtitle box to pop up in lower-center for 3 seconds with sample text")
    args = parser.parse_args()

    if args.preview:
        sample_subtitle = "สวัสดีครับเจมิไน พร้อมช่วยเหลือคุณแล้วครับ (TikTok Subtitle Preview)"
        print("[HUD Preview] Forcing center-bottom TikTok response subtitle box to pop up for 3.0s...")
        print(f"  - Subtitle: \"{sample_subtitle}\"")
        print("  - Mode: SPEAKING (Transparent, Click-through, Topmost z-order)")
        exit_code = show_hud(
            mode="SPEAKING",
            text="[SPEAKING] กำลังตอบกลับ (กด F20 เพื่อขัดจังหวะ).",
            duration=3.0,
            subtitle=sample_subtitle,
            target_box=None
        )
        print(f"[HUD Preview] Preview closed successfully (Exit Code {exit_code}).")
        sys.exit(exit_code)

    duration = 0.3 if args.test_mode else args.duration
    cursor = (args.x, args.y) if (args.x is not None and args.y is not None) else get_current_cursor_pos()

    target_box_tuple = None
    if args.target_box:
        try:
            parts = [float(p.strip()) for p in args.target_box.split(",") if p.strip()]
            if len(parts) == 4:
                target_box_tuple = (parts[0], parts[1], parts[2], parts[3])
        except Exception:
            pass

    print(f"[HUD Overlay] Starting visual overlay (Mode: {args.mode})...")
    print(f"  - Cursor Coordinates: {cursor}")
    print(f"  - Auto-close timer: {duration * 1000:.0f}ms (QTimer)")

    exit_code = show_hud(
        mode=args.mode,
        text=args.text,
        duration=duration,
        cursor_pos=cursor,
        action_name=args.action_name,
        command=args.command,
        phrase=args.phrase,
        subtitle=args.subtitle,
        target_box=target_box_tuple
    )

    print(f"[HUD Overlay] Window cleanly closed and memory freed (Exit Code {exit_code}).")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
