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
import argparse
import ctypes
from typing import Optional, Tuple

# Enable per-monitor DPI awareness before Qt / Win32 initializes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # Per-monitor DPI aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import win32api
import win32gui
import win32con

from PyQt6.QtWidgets import QApplication, QWidget
from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter,
    QColor,
    QPen,
    QBrush,
    QFont,
    QFontMetrics,
    QCursor
)


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


class HUDOverlayWindow(QWidget):
    """
    Full-screen, transparent, click-through, always-on-top HUD window.
    Renders cursor-anchored HUD elements without stealing focus or blocking inputs.
    """

    MODE_ACTION = "ACTION"
    MODE_THINKING = "THINKING"
    MODE_ERROR = "ERROR"

    DEFAULT_BODY_TEXT = (
        "[COGNITIVE ANCHOR: ONLINE]\n"
        "Target: Local Windows Edge"
    )

    def __init__(
        self,
        mode: str = "ACTION",
        text: Optional[str] = None,
        duration: float = 3.0,
        cursor_pos: Optional[Tuple[int, int]] = None
    ):
        super().__init__()
        self.mode = mode.upper()
        self.text = text if text is not None else self.DEFAULT_BODY_TEXT
        self.duration = duration
        self.cursor_pos = cursor_pos if cursor_pos is not None else get_current_cursor_pos()

        self._init_window()

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

        # Auto-close and cleanup timer (default: 3000ms)
        if self.duration > 0:
            QTimer.singleShot(int(self.duration * 1000), self.close_and_exit)

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_native_win32_styles()

    def _apply_native_win32_styles(self):
        """
        Enforce WS_EX_TRANSPARENT, WS_EX_LAYERED, WS_EX_TOOLWINDOW, and WS_EX_NOACTIVATE
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
                | win32con.WS_EX_NOACTIVATE
            )
            win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, new_style)
        except Exception as e:
            sys.stderr.write(f"[HUD] Warning applying Win32 styles: {e}\n")

    def close_and_exit(self):
        """Clean up window, quit Qt event loop, and free process memory."""
        self.close()
        app = QApplication.instance()
        if app:
            app.quit()
        gc.collect()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # Translate cursor to local coordinate space inside virtual screen geometry
        cx = float(self.cursor_pos[0] - self.screen_geom.x())
        cy = float(self.cursor_pos[1] - self.screen_geom.y())

        if self.mode == self.MODE_THINKING:
            # Subtle cyan/sky thinking focus ring
            self._draw_thinking_circle(painter, cx, cy)
            # Offset +20px for Thinking mode
            self._draw_cognitive_anchor_card(painter, cx, cy, offset=20)
        elif self.mode == self.MODE_ERROR:
            # Crimson-red target circle (radius 30px, line width 3px) with error details
            self._draw_red_target_circle(painter, cx, cy)
            # Offset +30px for Error mode
            self._draw_cognitive_anchor_card(painter, cx, cy, offset=30)
        else:
            # Emerald-green target circle (radius 30px, line width 3px)
            self._draw_emerald_target_circle(painter, cx, cy)
            # Offset +30px for Action mode
            self._draw_cognitive_anchor_card(painter, cx, cy, offset=30)

        painter.end()

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
        if self.mode == self.MODE_THINKING:
            card_border = QColor(56, 189, 248, 160)  # Sky/cyan accent border
            glow_color = QColor(56, 189, 248, 35)
        elif self.mode == self.MODE_ERROR:
            card_border = QColor(239, 68, 68, 180)  # Red error accent border
            glow_color = QColor(239, 68, 68, 45)
        else:
            card_border = QColor(16, 185, 129, 150)  # Emerald accent border
            glow_color = QColor(16, 185, 129, 35)

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

            if "[COGNITIVE ANCHOR" in line.upper():
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
    cursor_pos: Optional[Tuple[int, int]] = None
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

    window = HUDOverlayWindow(mode=mode, text=text, duration=duration, cursor_pos=cursor_pos)
    window.show()

    if should_exec:
        return app.exec()
    return 0


def main():
    parser = argparse.ArgumentParser(description="Windows Edge - Skeleton & HUD Visual Prototype")
    parser.add_argument("--mode", choices=["ACTION", "THINKING", "ERROR"], default="ACTION", help="Visual display mode")
    parser.add_argument("--text", type=str, default=None, help="Monospace body text / payload")
    parser.add_argument("--duration", type=float, default=3.0, help="Auto-close duration in seconds (default: 3.0s / 3000ms)")
    parser.add_argument("--x", type=int, default=None, help="Custom cursor X coordinate")
    parser.add_argument("--y", type=int, default=None, help="Custom cursor Y coordinate")
    parser.add_argument("--test-mode", action="store_true", help="Quick self-test validation mode (0.3s duration)")
    args = parser.parse_args()

    duration = 0.3 if args.test_mode else args.duration
    cursor = (args.x, args.y) if (args.x is not None and args.y is not None) else get_current_cursor_pos()

    print(f"[HUD Overlay] Starting visual prototype...")
    print(f"  - Cursor Coordinates: {cursor}")
    print(f"  - Target Circle: Emerald-green (radius 30px, line width 3px)")
    print(f"  - Cognitive Anchor Card: rgba(20, 24, 30, 0.85) at +30px, +30px")
    print(f"  - Auto-close timer: {duration * 1000:.0f}ms (QTimer)")

    exit_code = show_hud(mode=args.mode, text=args.text, duration=duration, cursor_pos=cursor)

    print(f"[HUD Overlay] Window cleanly closed and memory freed (Exit Code {exit_code}).")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
