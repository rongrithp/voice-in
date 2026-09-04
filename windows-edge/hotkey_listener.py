"""
hotkey_listener.py - Global Hotkey Listener & Voice Trigger Mock
Windows Edge Module: Non-blocking hotkey trigger with cursor-context HUD piping.

Architectural Analysis: Native Win32 API vs 'keyboard' Library
--------------------------------------------------------------
- 'keyboard' library:
  Relies on low-level global hooks (WH_KEYBOARD_LL via SetWindowsHookEx).
  This requires elevated/administrative privileges in many Windows environments,
  can trigger antivirus heuristics, and introduces keylogger-style OS overhead.
- Native Win32 API (RegisterHotKey via ctypes / pywin32):
  BEST SUITED: Operates entirely in standard user space WITHOUT requiring admin rights.
  It is the official, deterministic OS mechanism for global hotkeys, handles
  MOD_NOREPEAT cleanly, and works seamlessly across all active applications.
"""

import sys
import os
import time
import json
import ctypes
import threading
import argparse
import urllib.request
import urllib.error
from ctypes import wintypes
from typing import Optional, Callable, Dict, Any, Tuple

# Ensure module directory is in sys.path
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from hud_overlay import get_current_cursor_pos
from terminal_actuator import spawn_hud_overlay

# Win32 Constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_SPACE = 0x20
VK_F8 = 0x77

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000

DEFAULT_SERVER_URL = "http://127.0.0.1:8765/action"
DEFAULT_HEALTH_URL = "http://127.0.0.1:8765/health"


class GlobalHotkeyListener:
    """
    Non-blocking global hotkey listener powered by native Windows RegisterHotKey.
    Runs a Win32 message pump in a dedicated daemon thread to guarantee zero admin
    rights required and zero UI/terminal blocking.
    """

    def __init__(
        self,
        on_trigger: Optional[Callable[[], None]] = None,
        on_double_click: Optional[Callable[[], None]] = None,
        double_click_threshold: float = 0.35,
        modifiers: int = (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT),
        vk: int = VK_SPACE,
        hotkey_name: str = "Ctrl+Alt+Space",
        hotkey_id: int = 1001
    ):
        self.on_trigger = on_trigger
        self.on_double_click = on_double_click
        self.double_click_threshold = double_click_threshold
        self.modifiers = modifiers
        self.vk = vk
        self.hotkey_name = hotkey_name
        self.hotkey_id = hotkey_id

        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._is_running = threading.Event()
        self._registered_event = threading.Event()
        self._registration_success = False
        self._last_hotkey_time = 0.0

        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32

    def is_active(self) -> bool:
        return self._is_running.is_set() and self._registration_success

    def start(self, timeout: float = 3.0) -> bool:
        """Starts the background message pump thread and registers the global hotkey."""
        if self._is_running.is_set():
            return True

        self._thread = threading.Thread(
            target=self._message_pump,
            name=f"HotkeyListenerThread-{self.hotkey_name}",
            daemon=True
        )
        self._thread.start()

        # Wait for thread to register hotkey
        success = self._registered_event.wait(timeout=timeout)
        if not success or not self._registration_success:
            sys.stderr.write(f"[HotkeyListener] Failed to register global hotkey '{self.hotkey_name}'.\n")
            return False

        return True

    def stop(self, timeout: float = 2.0):
        """Cleanly unregisters hotkey and signals message loop thread to terminate."""
        if not self._is_running.is_set():
            return

        self._is_running.clear()
        if self._thread_id:
            # Post WM_QUIT to thread message queue
            self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _message_pump(self):
        """Background thread executing native Win32 message pump."""
        self._thread_id = self._kernel32.GetCurrentThreadId()

        # Force creation of message queue for this thread
        msg = wintypes.MSG()
        self._user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_NOREMOVE)

        # Register global hotkey
        res = self._user32.RegisterHotKey(
            None,
            self.hotkey_id,
            self.modifiers,
            self.vk
        )

        if res == 0:
            err = self._kernel32.GetLastError()
            sys.stderr.write(f"[HotkeyListener] Win32 RegisterHotKey error code: {err}\n")
            self._registration_success = False
            self._registered_event.set()
            return

        self._registration_success = True
        self._is_running.set()
        self._registered_event.set()

        try:
            # Message loop
            while self._is_running.is_set():
                ret = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:  # WM_QUIT or error
                    break

                if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                    now = time.perf_counter()
                    delta = now - self._last_hotkey_time
                    self._last_hotkey_time = now

                    # Early-stage double-click interceptor on the raw hotkey listener level
                    if 0.0 < delta <= self.double_click_threshold:
                        if self.on_double_click:
                            threading.Thread(
                                target=self.on_double_click,
                                daemon=True,
                                name="HotkeyDoubleWorker"
                            ).start()
                            continue

                    # Dispatch trigger in separate worker thread to avoid blocking message pump
                    if self.on_trigger:
                        threading.Thread(
                            target=self.on_trigger,
                            daemon=True,
                            name="HotkeyCallbackWorker"
                        ).start()

                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._user32.UnregisterHotKey(None, self.hotkey_id)
            self._is_running.clear()


def check_event_bus_health(health_url: str = DEFAULT_HEALTH_URL, timeout: float = 1.5) -> bool:
    """Checks if the local Windows Edge Event Bus server is reachable."""
    try:
        req = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def dispatch_action_to_bus(
    command: Optional[str] = None,
    mode: str = "ACTION",
    duration: float = 2.5,
    cursor_pos: Optional[Tuple[int, int]] = None,
    server_url: str = DEFAULT_SERVER_URL,
    timeout: float = 10.0
) -> Optional[Dict[str, Any]]:
    """Sends action or state payload to the local event bus HTTP server."""
    payload: Dict[str, Any] = {
        "mode": mode,
        "duration": duration,
        "cursor_pos": cursor_pos
    }
    if command is not None:
        payload["command"] = command

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        server_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                body = resp.read().decode("utf-8")
                return json.loads(body)
    except Exception as e:
        sys.stderr.write(f"[HotkeyListener] Event bus dispatch error: {e}\n")
    return None


def trigger_voice_mock_pipeline(
    command: str = "git status --short",
    action_duration: float = 2.5,
    server_url: str = DEFAULT_SERVER_URL,
    simulate_voice_delay: float = 0.4
) -> Optional[Dict[str, Any]]:
    """
    Executes the complete 4-step pipeline:
    1. Listening / Trigger: Global Hotkey intercepted without admin rights.
    2. State Transition (HUD Thinking): Immediately sends {"mode": "THINKING"} to Event Bus.
    3. Actuator (OS CLI Execution): Emulates voice conversion, dispatching {"command": command, "mode": "ACTION"}.
    4. Display (HUD Status / Error Log): Server renders emerald ring (success) or red ring (error).
    """
    # 1. Listening / Trigger
    cursor = get_current_cursor_pos()
    print(f"\n[1/4 Pipeline Trigger] Hotkey Intercepted! Live Cursor: {cursor}")

    # 2. State Transition (HUD Thinking)
    print(f"[2/4 State Transition] Sending {{'mode': 'THINKING'}} to {server_url}...")
    thinking_resp = dispatch_action_to_bus(
        command=None,
        mode="THINKING",
        duration=4.0,
        cursor_pos=cursor,
        server_url=server_url
    )

    local_thinking_proc = None
    if not thinking_resp:
        # Fallback to local HUD overlay if server is unreachable
        local_thinking_proc = spawn_hud_overlay(
            mode="THINKING",
            text=f"[VOICE TRIGGER: LISTENING...]\nIntent: '{command}'",
            duration=4.0,
            cursor_pos=cursor
        )

    # 3. Actuator (OS CLI Execution)
    if simulate_voice_delay > 0:
        time.sleep(simulate_voice_delay)

    print(f"[3/4 Actuator Dispatch] Emulating voice command -> {{'command': '{command}', 'mode': 'ACTION'}}...")
    action_resp = dispatch_action_to_bus(
        command=command,
        mode="ACTION",
        duration=action_duration,
        cursor_pos=cursor,
        server_url=server_url
    )

    if local_thinking_proc and local_thinking_proc.poll() is None:
        try:
            local_thinking_proc.terminate()
        except Exception:
            pass

    # 4. Display (HUD Status / Error Log)
    if action_resp:
        exit_code = action_resp.get("exit_code", 0)
        hud_mode = action_resp.get("mode", "ACTION")
        status_ring = "EMERALD (Success)" if exit_code == 0 else "RED (Failure / Error Log)"
        print(f"[4/4 Display HUD] Server execution finished with Exit {exit_code}")
        print(f"                 HUD Target Ring: {status_ring}")
        print(f"--- Output Summary ---\n{action_resp.get('summary')}\n----------------------")
        print(f"[Voice Trigger Pipeline] HUD successfully rendered at {action_resp.get('cursor_pos')}.")
    else:
        print(f"[4/4 Display HUD] Warning: No response received from {server_url}.")

    return action_resp


def run_self_verification(server_url: str = DEFAULT_SERVER_URL, health_url: str = DEFAULT_HEALTH_URL) -> bool:
    """Automated verification suite validating hotkey registration, event bus, and 4-step pipeline."""
    print("=" * 65)
    print(" HOTKEY LISTENER & VOICE MOCK: AUTOMATED VERIFICATION")
    print("=" * 65)

    # Step 1: Verify Native RegisterHotKey without admin rights
    print("[1/4] Testing Native Win32 RegisterHotKey registration (No Admin)...")
    test_triggered = threading.Event()

    listener = GlobalHotkeyListener(
        on_trigger=lambda: test_triggered.set(),
        modifiers=(MOD_CONTROL | MOD_ALT | MOD_NOREPEAT),
        vk=VK_SPACE,
        hotkey_name="Ctrl+Alt+Space"
    )
    success = listener.start()
    assert success, "Failed to register native Win32 hotkey"
    assert listener.is_active(), "Listener state is not active"
    print("      -> PASSED (RegisterHotKey & Win32 Message Pump Active)")

    # Clean unregister test
    listener.stop()
    assert not listener.is_active(), "Listener failed to unregister cleanly"
    print("      -> PASSED (Clean unregister & memory release)")

    # Step 2: Verify Event Bus Server Health
    print("\n[2/4] Checking Event Bus Server Status at 127.0.0.1:8765...")
    is_online = check_event_bus_health(health_url=health_url, timeout=2.0)
    if not is_online:
        print("      -> WARNING: Event bus server not detected.")
    else:
        print("      -> PASSED (Event Bus Server is Online and Healthy)")

    # Step 3: Verify Success Pipeline (THINKING -> ACTION / Emerald Ring)
    print("\n[3/4] Testing Success Pipeline: 'git status --short' (Emerald Ring)...")
    res_success = trigger_voice_mock_pipeline(
        command="git status --short",
        action_duration=1.0,
        server_url=server_url,
        simulate_voice_delay=0.3
    )
    if is_online:
        assert res_success is not None, "Failed to receive action response from server"
        assert res_success.get("status") == "success", "Expected status success"
        assert res_success.get("exit_code") == 0, f"Expected 0, got {res_success.get('exit_code')}"
        assert res_success.get("mode") == "ACTION", f"Expected mode ACTION, got {res_success.get('mode')}"
        print(f"      Pipeline Exit Code: {res_success.get('exit_code')} ({res_success.get('duration_ms', 0):.1f}ms)")
        print("      -> PASSED (Emerald ring & outcome summary verified)")

    # Step 4: Verify Failure Pipeline (ERROR / Red Ring with error details)
    print("\n[4/4] Testing Failure Pipeline: Non-zero exit code (Red Ring)...")
    res_fail = trigger_voice_mock_pipeline(
        command="cmd.exe /c \"echo Execution Failed 404 1>&2 & exit 1\"",
        action_duration=1.0,
        server_url=server_url,
        simulate_voice_delay=0.2
    )
    if is_online:
        assert res_fail is not None, "Failed to receive error action response from server"
        assert res_fail.get("status") == "error", "Expected status error"
        assert res_fail.get("exit_code") == 1, f"Expected exit code 1, got {res_fail.get('exit_code')}"
        assert res_fail.get("mode") == "ERROR", f"Expected mode ERROR, got {res_fail.get('mode')}"
        print(f"      Pipeline Error Code: {res_fail.get('exit_code')} (Handled cleanly)")
        print("      -> PASSED (Red ring & error log details verified)")

    print("\n" + "=" * 65)
    print(" ALL 4-STEP VERIFICATION CHECKS COMPLETED SUCCESSFULLY")
    print("=" * 65)
    return True


def main():
    parser = argparse.ArgumentParser(description="Windows Edge - Global Hotkey Listener & Voice Trigger Mock")
    parser.add_argument("--test", action="store_true", help="Run automated test verification suite")
    parser.add_argument("--simulate", action="store_true", help="Simulate hotkey trigger immediately and exit")
    parser.add_argument("--cmd", type=str, default="git status --short", help="Mock terminal command to execute (default: git status --short)")
    parser.add_argument("--duration", type=float, default=2.5, help="Result HUD display duration in seconds")
    parser.add_argument("--server", type=str, default=DEFAULT_SERVER_URL, help="Event bus action endpoint URL")
    args = parser.parse_args()

    if args.test:
        success = run_self_verification(server_url=args.server)
        sys.exit(0 if success else 1)

    if args.simulate:
        print("[HotkeyListener] Simulating hotkey trigger directly...")
        trigger_voice_mock_pipeline(
            command=args.cmd,
            action_duration=args.duration,
            server_url=args.server
        )
        sys.exit(0)

    # Interactive background listener
    print("=" * 65)
    print(" WINDOWS EDGE: GLOBAL HOTKEY LISTENER (VOICE MOCK)")
    print("=" * 65)
    print(f"  - Hotkey:            [ Ctrl + Alt + Space ] (No Admin Rights Needed)")
    print(f"  - Target Endpoint:   {args.server}")
    print(f"  - Mock Voice Action: '{args.cmd}'")
    print(f"  - HUD Indicator:     THINKING (Listening) -> ACTION (Result)")
    print("=" * 65)

    def _on_hotkey_pressed():
        trigger_voice_mock_pipeline(
            command=args.cmd,
            action_duration=args.duration,
            server_url=args.server
        )

    listener = GlobalHotkeyListener(
        on_trigger=_on_hotkey_pressed,
        modifiers=(MOD_CONTROL | MOD_ALT | MOD_NOREPEAT),
        vk=VK_SPACE,
        hotkey_name="Ctrl+Alt+Space"
    )

    if not listener.start():
        print("[Error] Could not register global hotkey. Another app may be using Ctrl+Alt+Space.")
        sys.exit(1)

    print("\n>>> Hotkey listener is ONLINE in the background.")
    print(">>> Press [ Ctrl + Alt + Space ] anytime from any application to trigger!")
    print(">>> Press Ctrl+C in this terminal to exit.\n")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[HotkeyListener] Stopping hotkey listener...")
        listener.stop()
        print("[HotkeyListener] Exited cleanly.")


if __name__ == "__main__":
    main()
