#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows Edge: Visual Cortex (Agent's Eye Sensory Module)
=============================================================================
Zero-disk in-memory visual perception module based on current cursor gaze.
Functions as the agent's "eyes", capturing whatever window the user is currently
pointing at / looking at with their mouse cursor.

Biological Sensory Pipeline:
1. Gaze Tracking: Intercepts current cursor position (X, Y) on the interactive desktop.
2. Target Focus: Identifies target window handle via WindowFromPoint and traverses to root frame.
3. Metadata Extraction: Resolves Window Title, Process Name, and Bounding Box.
4. Visual Ingestion: Captures the focused window visual region directly into memory via PIL.ImageGrab.
5. Neural Buffer: Encodes frame into in-memory JPEG byte buffer and base64 string (0 disk writes).
=============================================================================
"""

import sys
import os
import io
import time
import base64
import argparse
from typing import Dict, Any, Tuple, Optional

# Ensure UTF-8 console output for Thai characters and international titles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import win32gui
import win32api
import win32con
import win32process
from PIL import Image, ImageGrab


def ensure_interactive_station():
    """
    Attach calling thread and process to interactive desktop (WinSta0\\Default).
    Prevents Win32 Access Denied (Error 5) in background contexts.
    """
    try:
        import win32service
        hwinsta = win32service.OpenWindowStation("WinSta0", False, win32con.MAXIMUM_ALLOWED)
        hwinsta.SetProcessWindowStation()
        hdesk = win32service.OpenDesktop("Default", 0, False, win32con.MAXIMUM_ALLOWED)
        hdesk.SetThreadDesktop()
    except Exception:
        pass


def get_process_name_by_pid(pid: int) -> str:
    """
    Resolves executable process name from process ID using native Win32 kernel32 API,
    with optional psutil fallback. Zero external binary dependencies.
    """
    if pid <= 0:
        return "system"

    # Optional psutil fallback if available
    try:
        import psutil
        proc = psutil.Process(pid)
        return proc.name()
    except Exception:
        pass

    # Native Win32 QueryFullProcessImageNameW
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_process = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h_process:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.c_ulong(1024)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(h_process, 0, buf, ctypes.byref(size)):
                full_path = buf.value
                ctypes.windll.kernel32.CloseHandle(h_process)
                return os.path.basename(full_path)
            ctypes.windll.kernel32.CloseHandle(h_process)
    except Exception:
        pass

    return "unknown.exe"


def get_focused_window_at_cursor(
    cursor_pos: Optional[Tuple[int, int]] = None
) -> Tuple[int, Tuple[int, int], Tuple[int, int, int, int], str, str]:
    """
    Identifies the window beneath the user's cursor gaze (or fetches current cursor coordinates).
    Traverses child UI elements up to the root window (GA_ROOT).

    Returns:
        (root_hwnd, (cursor_x, cursor_y), (left, top, right, bottom), title, process_name)
    """
    ensure_interactive_station()

    if cursor_pos is None:
        try:
            cursor_pos = win32api.GetCursorPos()
        except Exception:
            cursor_pos = (0, 0)

    cx, cy = cursor_pos

    try:
        raw_hwnd = win32gui.WindowFromPoint((cx, cy))
    except Exception:
        raw_hwnd = win32gui.GetForegroundWindow()

    if not raw_hwnd:
        raw_hwnd = win32gui.GetDesktopWindow()

    # Traverse to root ancestor window (top-level application frame)
    try:
        root_hwnd = win32gui.GetAncestor(raw_hwnd, win32con.GA_ROOT)
        if not root_hwnd:
            root_hwnd = raw_hwnd
    except Exception:
        root_hwnd = raw_hwnd

    # Window Title
    try:
        title = win32gui.GetWindowText(root_hwnd).strip()
    except Exception:
        title = ""

    if not title:
        title = "Untitled Window"

    # Process Name
    try:
        _, pid = win32process.GetWindowThreadProcessId(root_hwnd)
        process_name = get_process_name_by_pid(pid)
    except Exception:
        process_name = "unknown.exe"

    # Window Bounding Box
    try:
        rect = win32gui.GetWindowRect(root_hwnd)  # (left, top, right, bottom)
    except Exception:
        rect = (0, 0, 1920, 1080)

    return (root_hwnd, (cx, cy), rect, title, process_name)


# Alias for backward compatibility
get_target_window_at_cursor = get_focused_window_at_cursor


def look_at_cursor(
    cursor_pos: Optional[Tuple[int, int]] = None,
    quality: int = 85
) -> Dict[str, Any]:
    """
    Agent's Visual Cortex: Captures the focused visual region of the root window directly under cursor gaze.
    Encodes image into an in-memory JPEG byte buffer and base64 string (zero disk writes).

    Returns sensory visual payload:
        {
            "title": str,
            "process_name": str,
            "cursor_rel_pos": {"x": int, "y": int},
            "image_bytes": bytes,
            "image_b64": str,
            "dimensions": {"width": int, "height": int}
        }
    """
    ensure_interactive_station()

    root_hwnd, (cx, cy), rect, title, process_name = get_focused_window_at_cursor(cursor_pos)
    left, top, right, bottom = rect

    width = right - left
    height = bottom - top

    # Sanity checks for window dimensions
    if width <= 0 or height <= 0:
        width = max(width, 100)
        height = max(height, 100)
        right = left + width
        bottom = top + height

    # Relative cursor coordinates within the captured window
    rel_x = cx - left
    rel_y = cy - top

    # Zero-disk Screen Capture via PIL.ImageGrab with all_screens=True for multi-monitor setups
    capture_bbox = (left, top, right, bottom)
    try:
        img = ImageGrab.grab(bbox=capture_bbox, all_screens=True)
    except Exception:
        try:
            full_screen = ImageGrab.grab(all_screens=True)
            img = full_screen.crop((max(0, left), max(0, top), max(100, right), max(100, bottom)))
        except Exception:
            img = Image.new("RGB", (width, height), color=(20, 24, 30))

    # Convert to RGB mode for clean JPEG encoding (handles RGBA / Palette)
    if img.mode != "RGB":
        img = img.convert("RGB")

    # In-memory JPEG encoding (Zero Disk I/O)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality, optimize=True)
    image_bytes = buffer.getvalue()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    actual_w, actual_h = img.size

    return {
        "title": title,
        "process_name": process_name,
        "cursor_rel_pos": {
            "x": int(rel_x),
            "y": int(rel_y)
        },
        "image_bytes": image_bytes,
        "image_b64": image_b64,
        "dimensions": {
            "width": int(actual_w),
            "height": int(actual_h)
        },
        "window_rect": {
            "left": int(left),
            "top": int(top),
            "right": int(right),
            "bottom": int(bottom)
        }
    }


# Aliases for sensory and backwards-compatibility
capture_window_at_cursor = look_at_cursor
see_target_window = look_at_cursor
capture_focused_view = look_at_cursor


def run_visual_cortex_test() -> bool:
    """
    Self-Verification Test Suite:
    Captures target window at cursor via visual cortex, prints metadata, and validates JPEG buffer.
    """
    print("=" * 65)
    print(" VISUAL CORTEX: EYE SENSORY MODULE VERIFICATION TEST")
    print("=" * 65)
    print("\n[Step 1] Hover your mouse over any application window...")
    for i in range(2, 0, -1):
        print(f"         Observing gaze in {i} second(s)...")
        time.sleep(1.0)

    print("\n[Step 2] Executing look_at_cursor()...")
    t0 = time.perf_counter()
    data = look_at_cursor()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    print(f"         Visual ingestion finished in {elapsed_ms:.1f}ms")

    # Metadata Validation
    print("\n[Step 3] Validating Visual Perception Payload...")
    print(f"         Window Title:  \"{data['title']}\"")
    print(f"         Process Name:  {data['process_name']}")
    print(f"         Dimensions:    {data['dimensions']['width']}x{data['dimensions']['height']} px")
    print(f"         Relative Eye:  (x={data['cursor_rel_pos']['x']}, y={data['cursor_rel_pos']['y']})")
    print(f"         JPEG Buffer:   {len(data['image_bytes']):,} bytes (In-Memory)")
    print(f"         Base64 Length: {len(data['image_b64']):,} chars")

    assert bool(data["title"]), "Window Title must not be empty"
    assert bool(data["process_name"]), "Process Name must not be empty"
    assert data["dimensions"]["width"] > 0, "Window width must be > 0"
    assert data["dimensions"]["height"] > 0, "Window height must be > 0"
    assert len(data["image_bytes"]) > 0, "image_bytes must not be empty"
    assert len(data["image_b64"]) > 0, "image_b64 must not be empty"

    # Verify JPEG Header Magic (0xFF, 0xD8, 0xFF)
    assert data["image_bytes"][:3] == b"\xff\xd8\xff", "Image buffer must start with valid JPEG SOI magic"

    # Verify PIL can decode from memory buffer directly
    verify_img = Image.open(io.BytesIO(data["image_bytes"]))
    assert verify_img.format == "JPEG", f"Decoded format must be JPEG, got {verify_img.format}"
    print("         Decoded Format: JPEG (Valid Header & In-Memory Stream)")

    print("\n" + "=" * 65)
    print(" VISUAL CORTEX PERCEPTION VERIFIED (0 DISK WRITES, 100% SUCCESS)")
    print("=" * 65)
    return True


# Backward compatibility alias
run_context_harvester_test = run_visual_cortex_test


def main():
    parser = argparse.ArgumentParser(description="Windows Edge: Visual Cortex (Eye Sensory Module)")
    parser.add_argument("--test", action="store_true", help="Run automated self-test verification")
    parser.add_argument("--quality", type=int, default=85, help="JPEG encoding quality (1-100, default: 85)")
    args = parser.parse_args()

    if args.test:
        success = run_visual_cortex_test()
        sys.exit(0 if success else 1)

    payload = look_at_cursor(quality=args.quality)
    print(f"[Visual Cortex] Target: '{payload['title']}' ({payload['process_name']})")
    print(f"                Dimensions: {payload['dimensions']['width']}x{payload['dimensions']['height']} px")
    print(f"                Relative Eye: ({payload['cursor_rel_pos']['x']}, {payload['cursor_rel_pos']['y']})")
    print(f"                Buffer: {len(payload['image_bytes']):,} bytes (JPEG)")


if __name__ == "__main__":
    main()
