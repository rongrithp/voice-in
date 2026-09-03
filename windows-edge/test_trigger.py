"""
test_trigger.py - Event Bus Client Verification
Sends a test POST request to http://127.0.0.1:8765/action with {"command": "echo Trigger Received", "mode": "ACTION"}
and verifies the server execution response and cursor HUD trigger.
"""

import sys
import json
import urllib.request
import urllib.error
import time

SERVER_URL = "http://127.0.0.1:8765/action"
HEALTH_URL = "http://127.0.0.1:8765/health"


def wait_for_server(timeout: float = 5.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(HEALTH_URL, method="GET")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def send_action_trigger(command: str = "echo Trigger Received", mode: str = "ACTION", duration: float = 2.5):
    print("=" * 60)
    print(" WINDOWS EDGE: EVENT BUS TRIGGER TEST")
    print("=" * 60)
    print(f"Target: {SERVER_URL}")
    print(f"Payload Command: '{command}'")
    print(f"Payload Mode:    '{mode}'")

    payload = {
        "command": command,
        "mode": mode,
        "duration": duration
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        SERVER_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            status_code = resp.status
            body = resp.read().decode("utf-8")
            res_json = json.loads(body)

        print(f"\n[HTTP Response] Status: {status_code} ({elapsed_ms:.1f}ms)")
        print(f"[Execution Summary]:\n{res_json.get('summary', '')}")
        print(f"[Target Cursor]:     {res_json.get('cursor_pos')}")
        print(f"[Exit Code]:         {res_json.get('exit_code')}")

        assert status_code == 200, f"Expected 200, got {status_code}"
        assert res_json.get("status") == "success", "Response status was not 'success'"
        assert res_json.get("exit_code") == 0, f"Command exit code was {res_json.get('exit_code')}"

        print("\n" + "=" * 60)
        print(" [SUCCESS] Trigger Verified & HUD Overlay Dispatched!")
        print("=" * 60)
        return True

    except urllib.error.URLError as e:
        print(f"\n[Error] Connection to {SERVER_URL} failed: {e}")
        return False
    except Exception as e:
        print(f"\n[Error] Unexpected exception: {e}")
        return False


if __name__ == "__main__":
    if not wait_for_server(timeout=3.0):
        print(f"[Error] Server at {SERVER_URL} is not reachable. Ensure server.py is running.")
        sys.exit(1)

    cmd = sys.argv[1] if len(sys.argv) > 1 else "echo Trigger Received"
    mode = sys.argv[2] if len(sys.argv) > 2 else "ACTION"
    success = send_action_trigger(command=cmd, mode=mode)
    sys.exit(0 if success else 1)
