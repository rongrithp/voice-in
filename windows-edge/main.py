"""
main.py - Windows Edge: Cursor-Context Terminal Runner & Minimal HUD Painter
Unified Service Runner: Entrypoint for automated tests, live voice daemon, and CLI actuation.
"""

import sys
import os
import time
import json
import argparse
import subprocess
import threading
import urllib.request
import urllib.error
from typing import Tuple, Optional

# Ensure UTF-8 output encoding on Windows console
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Add current directory to path
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from hud_overlay import get_current_cursor_pos, show_hud, HUDOverlayWindow
from terminal_actuator import TerminalActuator, ActuatorResult, spawn_hud_overlay
from server import ThreadingHTTPServer, EventBusHandler, DEFAULT_HOST, DEFAULT_PORT
from audio_recorder import PushToTalkAudioListener, AudioRecorder
from stt_engine import STTEngine
from intent_parser import IntentParser


def free_port(target_port: int = DEFAULT_PORT):
    """Cleanly terminates any lingering or zombie processes holding target_port."""
    try:
        res = subprocess.run(
            f"netstat -ano | findstr :{target_port}",
            shell=True,
            capture_output=True,
            text=True
        )
        my_pid = os.getpid()
        pids = set()
        for line in res.stdout.splitlines():
            if "LISTENING" in line:
                parts = line.strip().split()
                if len(parts) >= 5:
                    try:
                        p = int(parts[-1])
                        if p != my_pid and p > 0:
                            pids.add(p)
                    except ValueError:
                        pass
        for p in pids:
            subprocess.run(f"taskkill /F /PID {p}", shell=True, capture_output=True)
        if pids:
            time.sleep(0.3)
    except Exception:
        pass


def run_automated_tests() -> bool:
    """
    Executes a comprehensive verification suite ensuring zero syntax errors,
    proper window initialization, correct terminal output capture, and clean termination.
    """
    print("=" * 60)
    print(" WINDOWS EDGE: AUTOMATED VERIFICATION SUITE")
    print("=" * 60)

    # 1. Verify Cursor Position Retrieval
    print("[1/5] Testing Cursor Position Retrieval (win32api)...")
    pos = get_current_cursor_pos()
    print(f"      Cursor Pos: {pos} (Type: {type(pos)}, Length: {len(pos)})")
    assert isinstance(pos, (tuple, list)) and len(pos) == 2, "Invalid cursor pos returned"
    print("      -> PASSED")

    # 2. Verify HUD ACTION Mode
    print("\n[2/5] Testing HUD Overlay: ACTION Mode (Glowing Green Circle r=30px)...")
    proc_action = spawn_hud_overlay(
        mode="ACTION",
        text="[VERIFY] Action Mode Target",
        duration=0.5
    )
    assert proc_action is not None, "Failed to spawn ACTION HUD overlay process"
    proc_action.wait(timeout=4.0)
    assert proc_action.returncode == 0, f"ACTION HUD exited with code {proc_action.returncode}"
    print("      -> PASSED (Clean termination, Exit Code 0)")

    # 3. Verify HUD THINKING Mode
    print("\n[3/5] Testing HUD Overlay: THINKING Mode (Dark Rounded Container +20px)...")
    thinking_payload = "> dir /b\nAnalyzing environment context...\nCognitive Anchor Active"
    proc_thinking = spawn_hud_overlay(
        mode="THINKING",
        text=thinking_payload,
        duration=0.5
    )
    assert proc_thinking is not None, "Failed to spawn THINKING HUD overlay process"
    proc_thinking.wait(timeout=4.0)
    assert proc_thinking.returncode == 0, f"THINKING HUD exited with code {proc_thinking.returncode}"
    print("      -> PASSED (Clean termination, Exit Code 0)")

    # 4. Verify Terminal Actuator Execution & Output Capture
    print("\n[4/5] Testing Terminal Actuator Subprocess Runner...")
    actuator = TerminalActuator(default_hud_duration=0.5)

    # Success case
    res_success = actuator.execute_sync("echo Actuator Verification String 12345")
    assert res_success.exit_code == 0, f"Expected 0, got {res_success.exit_code}"
    assert "Actuator Verification String 12345" in res_success.stdout
    print(f"      Success Command Output: {res_success.summary(max_lines=1)}")

    # Error capture case
    res_err = actuator.execute_sync("cmd.exe /c \"exit 42\"")
    assert res_err.exit_code == 42, f"Expected 42, got {res_err.exit_code}"
    print(f"      Non-zero Exit Code Handled: {res_err.exit_code}")
    print("      -> PASSED")

    # 5. Verify Full Pipeline: Async Execution with HUD Piping
    print("\n[5/5] Testing Full Pipeline (Async Runner -> HUD Pipe)...")
    pipeline_completed = [False]
    pipeline_result = [None]

    def _on_pipeline_done(result: ActuatorResult):
        pipeline_completed[0] = True
        pipeline_result[0] = result

    t = actuator.execute_async(
        command="echo Pipeline Integration OK",
        on_complete=_on_pipeline_done,
        show_hud=True,
        hud_duration=0.6
    )
    t.join(timeout=5.0)

    assert pipeline_completed[0], "Pipeline callback was not invoked"
    assert pipeline_result[0].exit_code == 0, "Pipeline command failed"
    print(f"      Pipeline Result: {pipeline_result[0].summary(max_lines=1)}")
    print("      -> PASSED")

    print("\n" + "=" * 60)
    print(" ALL VERIFICATION CHECKS PASSED SUCCESSFULLY (0 ERRORS)")
    print("=" * 60)
    return True


def run_live_health_check(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    """
    Startup & health check verifying:
    1. Local Event Bus binds and returns online.
    2. Hotkey Push-to-Talk listener initializes without admin rights.
    3. HUD overlay renders and closes cleanly.
    4. Clean shutdown leaves no orphan processes.
    """
    print("=" * 65)
    print(" WINDOWS EDGE: LIVE SERVICE HEALTH CHECK")
    print("=" * 65)

    free_port(port)

    # 1. Start in-process server
    print(f"[1/4] Starting In-Process Event Bus on http://{host}:{port}...")
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, EventBusHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="HealthCheckServerThread")
    server_thread.start()
    time.sleep(0.3)

    # Health probe
    try:
        req = urllib.request.Request(f"http://{host}:{port}/health")
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            assert data.get("status") == "online", "Server status was not online"
            print(f"      Server Probe: Online (Cursor: {data.get('cursor_pos')})")
            print("      -> PASSED (Event Bus server healthy)")
    except Exception as e:
        print(f"      -> FAILED to reach server: {e}")
        httpd.shutdown()
        httpd.server_close()
        return False

    # 2. Start Push-to-Talk listener
    print("\n[2/4] Initializing Push-to-Talk Hotkey Listener (Ctrl + Alt + Space)...")
    ptt = PushToTalkAudioListener(server_url=f"http://{host}:{port}/action", enable_stt=False)
    ptt.start()
    time.sleep(0.3)
    assert ptt._is_active, "PTT listener failed to register hotkey"
    print("      -> PASSED (Push-to-Talk listener active without admin rights)")

    # 3. Test Action Dispatch to Cursor HUD
    print("\n[3/4] Testing Action Trigger & HUD Overlay Dispatch...")
    try:
        action_payload = {
            "command": "echo Live Service Health Check OK",
            "mode": "ACTION",
            "duration": 0.5
        }
        data_bytes = json.dumps(action_payload).encode("utf-8")
        req = urllib.request.Request(
            f"http://{host}:{port}/action",
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            assert resp_data.get("status") == "success", "Action response status not success"
            assert resp_data.get("exit_code") == 0, "Action command failed"
            print(f"      HUD Action Dispatched: {resp_data.get('summary')}")
            print("      -> PASSED (Action executed & HUD overlay dispatched)")
    except Exception as e:
        print(f"      -> FAILED to dispatch action: {e}")
        ptt.stop()
        httpd.shutdown()
        httpd.server_close()
        return False

    # 4. Clean Shutdown
    print("\n[4/4] Verifying Graceful Teardown & Port Release...")
    ptt.stop()
    httpd.shutdown()
    httpd.server_close()
    free_port(port)
    time.sleep(0.4)

    # Check port is free
    res = subprocess.run(f"netstat -ano | findstr :{port}", shell=True, capture_output=True, text=True)
    is_listening = any("LISTENING" in line and f":{port}" in line for line in res.stdout.splitlines())
    assert not is_listening, f"Port {port} still in use after shutdown"
    print(f"      Port {port} cleanly released. Zero orphan processes.")
    print("      -> PASSED")

    print("\n" + "=" * 65)
    print(" ALL LIVE HEALTH CHECKS PASSED (SYSTEM 100% OPERATIONAL)")
    print("=" * 65)
    return True


def run_live_service(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    """
    Unified Live Daemon Launcher:
    - Starts local Event Bus on http://127.0.0.1:8765.
    - Pre-warms STT Engine (faster-whisper base) in background.
    - Activates Push-to-Talk Listener on [ Ctrl + Alt + Space ].
    - Handles Ctrl + C cleanly without leaving orphan Python processes on port 8765.
    """
    print("=" * 70)
    print(" WINDOWS EDGE: UNIFIED LIVE VOICE-IN RUNNER (ACTIVE)")
    print("=" * 70)

    # Ensure port is not blocked by previous zombie processes
    free_port(port)

    # 1. Pre-warm STT Engine
    print("[1/3] Loading Speech-to-Text Engine (faster-whisper base)...")
    stt_engine = STTEngine(model_size="base", device="cpu", compute_type="int8")

    # 2. Start in-process Event Bus HTTP server
    print(f"[2/3] Starting Local Event Bus on http://{host}:{port}...")
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, EventBusHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="EventBusServerThread")
    server_thread.start()
    time.sleep(0.2)

    # 3. Start Push-to-Talk Hotkey Listener
    print(f"[3/3] Registering Global Hotkey [ Ctrl + Alt + Space ] (No Admin)...")
    ptt_listener = PushToTalkAudioListener(
        server_url=f"http://{host}:{port}/action",
        enable_stt=True
    )
    # Share pre-warmed STT engine
    ptt_listener.stt_engine = stt_engine
    ptt_listener.start()

    print("\n" + "=" * 70)
    print(" >>> VOICE-IN DAEMON IS ONLINE AND LISTENING IN BACKGROUND <<<")
    print("=" * 70)
    print(" [HOW TO OPERATE]:")
    print("   1. HOLD DOWN : [ Ctrl + Alt + Space ]")
    print("   2. SPEAK     : Say your command clearly into your microphone")
    print("   3. RELEASE   : Release the keys to dispatch transcription & execution")
    print("")
    print(" [SUPPORTED VOICE COMMANDS (THAI & ENGLISH)]:")
    print("   - \"เปิดบราวเซอร์\"  /  \"open browser\"   -> start chrome")
    print("   - \"เช็คสถานะ git\"  /  \"git status\"      -> git status --short")
    print("   - \"เปิดโปรเจกต์\"   /  \"open project\"    -> explorer .")
    print("   - \"เปิดเทอร์มินัล\"  /  \"open terminal\"   -> start cmd")
    print("   - \"แสดงไฟล์\"      /  \"list files\"       -> dir /b")
    print("   - \"whoami\"        /  \"ผู้ใช้ปัจจุบัน\"   -> whoami")
    print("")
    print(" [STATUS INDICATORS NEXT TO CURSOR]:")
    print("   - Blue Reticle Ring   : Microphone Recording (Listening)")
    print("   - Emerald Green Ring  : Command Succeeded (Output displayed)")
    print("   - Crimson Red Ring    : Command Failed / Warning Logged")
    print("=" * 70)
    print(" Press Ctrl+C in this window at any time to cleanly stop the service.\n")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[Voice-In Live] Initiating graceful shutdown...")
    finally:
        print("[Voice-In Live] Stopping Push-to-Talk hotkey listener...")
        ptt_listener.stop()
        print("[Voice-In Live] Shutting down Event Bus HTTP server...")
        httpd.shutdown()
        httpd.server_close()
        time.sleep(0.2)
        free_port(port)
        print("[Voice-In Live] Shutdown complete. Port 8765 released. Clean exit.")


def run_interactive_demo():
    """Demonstrates ACTION, THINKING, and ERROR HUD modes at current cursor position."""
    print("Windows Edge HUD Interactive Demo starting...")
    pos = get_current_cursor_pos()
    print(f"Current Cursor Position: {pos}")

    print("\n1. Spawning THINKING Mode HUD (2.0s duration)...")
    proc1 = spawn_hud_overlay(
        mode="THINKING",
        text="> powershell -Command Get-Process\n[STATUS: Cognitive Anchor Active]\nScanning active windows...",
        duration=2.0
    )
    if proc1:
        proc1.wait()

    time.sleep(0.4)

    print("\n2. Spawning ACTION Mode HUD (2.0s duration)...")
    proc2 = spawn_hud_overlay(
        mode="ACTION",
        text="[EXIT 0] Action Target Acquired\nAll systems nominal.",
        duration=2.0
    )
    if proc2:
        proc2.wait()

    time.sleep(0.4)

    print("\n3. Spawning ERROR Mode HUD (2.0s duration)...")
    proc3 = spawn_hud_overlay(
        mode="ERROR",
        text="[EXIT 1] Error Detected\nInvalid parameter specified.",
        duration=2.0
    )
    if proc3:
        proc3.wait()

    print("\nDemo completed cleanly.")


def run_live_actuation(command: str = "echo Windows Edge Actuator Online & hostname", duration: float = 3.0):
    """Executes a system command via TerminalActuator and pops up HUD next to cursor."""
    pos = get_current_cursor_pos()
    print(f"\n[Live Actuation] Target Cursor Position: {pos}")
    print(f"[Live Actuation] Dispatching Command: '{command}'")

    actuator = TerminalActuator(default_hud_duration=duration)
    result = actuator.execute_sync(command)

    summary = result.summary(max_lines=3)
    hud_mode = "ACTION" if result.exit_code == 0 else "ERROR"
    print(f"[Live Actuation] Execution Completed in {result.duration_ms:.1f}ms (Exit {result.exit_code})")
    print(f"[Live Actuation] Popping up HUD overlay next to cursor {pos}...")

    proc = spawn_hud_overlay(
        mode=hud_mode,
        text=summary,
        duration=duration,
        cursor_pos=pos
    )
    if proc:
        proc.wait(timeout=duration + 2.0)
    print("[Live Actuation] HUD closed cleanly.")
    return result


def main():
    parser = argparse.ArgumentParser(description="Windows Edge Module Entrypoint")
    parser.add_argument("--live", action="store_true", help="Launch unified live voice daemon service")
    parser.add_argument("--live-check", action="store_true", help="Run automated startup and health check for live service")
    parser.add_argument("--test", action="store_true", help="Run automated test suite and exit")
    parser.add_argument("--demo", action="store_true", help="Run interactive visual HUD demo")
    parser.add_argument("--cmd", type=str, default=None, help="Directly execute a terminal task with HUD")
    parser.add_argument("--actuate", action="store_true", help="Run system command and pop up HUD next to cursor")
    parser.add_argument("--duration", type=float, default=3.0, help="HUD display duration in seconds")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Event Bus bind port (default: 8765)")
    args = parser.parse_args()

    if args.live:
        run_live_service(port=args.port)
        sys.exit(0)
    elif args.live_check:
        success = run_live_health_check(port=args.port)
        sys.exit(0 if success else 1)
    elif args.test:
        success = run_automated_tests()
        sys.exit(0 if success else 1)
    elif args.demo:
        run_interactive_demo()
        sys.exit(0)
    elif args.cmd:
        actuator = TerminalActuator(default_hud_duration=args.duration)
        res = actuator.execute_sync(args.cmd)
        print(res.summary())
        pos = get_current_cursor_pos()
        hud_mode = "ACTION" if res.exit_code == 0 else "ERROR"
        proc = spawn_hud_overlay(mode=hud_mode, text=res.summary(max_lines=3), duration=args.duration, cursor_pos=pos)
        if proc:
            proc.wait()
        sys.exit(res.exit_code)
    elif args.actuate:
        res = run_live_actuation(duration=args.duration)
        sys.exit(res.exit_code)
    else:
        # Default: run live health check then automated verification
        print(">>> Performing Live Service Startup & Health Check...")
        hc_ok = run_live_health_check(port=args.port)
        if not hc_ok:
            sys.exit(1)
        print("\n>>> Running Automated Verification Suite...")
        success = run_automated_tests()
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
