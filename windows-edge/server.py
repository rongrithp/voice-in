"""
server.py - Lightweight Local Event Bus (Windows Edge)
HTTP Server listening on 127.0.0.1:8765 to receive external trigger payloads,
execute terminal commands via TerminalActuator, and pop up HUD overlay next to cursor.
"""

import sys
import os
import json
import time
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Tuple, Optional

# Ensure UTF-8 output encoding on Windows console
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Ignore console control signals (Ctrl+C / Ctrl+Break / Close) so daemon survives parent terminal cycles
try:
    import win32api
    def _console_ctrl_handler(ctrl_type):
        return True
    win32api.SetConsoleCtrlHandler(_console_ctrl_handler, True)
except Exception:
    pass

# Ensure module directory is in sys.path
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

from hud_overlay import get_current_cursor_pos
from terminal_actuator import TerminalActuator, spawn_hud_overlay

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded HTTP server so incoming requests never block one another."""
    daemon_threads = True
    allow_reuse_address = True


class EventBusHandler(BaseHTTPRequestHandler):
    actuator = TerminalActuator(default_hud_duration=2.5)
    active_thinking_proc = None

    def _send_json(self, status_code: int, data: dict):
        response_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/health"):
            self._send_json(200, {
                "status": "online",
                "service": "windows-edge-event-bus",
                "cursor_pos": get_current_cursor_pos()
            })
        else:
            self._send_json(404, {"status": "error", "message": "Not Found"})

    def do_POST(self):
        if self.path != "/action":
            self._send_json(404, {"status": "error", "message": "Unknown endpoint. Use POST /action"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(400, {"status": "error", "message": "Empty request body"})
            return

        try:
            body = self.rfile.read(content_length).decode("utf-8")
            payload = json.loads(body)
        except Exception as e:
            self._send_json(400, {"status": "error", "message": f"Malformed JSON: {e}"})
            return

        mode = payload.get("mode", "ACTION").upper()
        if mode not in ("ACTION", "THINKING", "ERROR"):
            mode = "ACTION"

        duration = float(payload.get("duration", 2.5))
        cursor_pos = payload.get("cursor_pos")
        if not (isinstance(cursor_pos, (list, tuple)) and len(cursor_pos) == 2):
            cursor_pos = get_current_cursor_pos()
        else:
            cursor_pos = (int(cursor_pos[0]), int(cursor_pos[1]))

        command = payload.get("command", "").strip()

        # Step 2: State Transition (HUD Thinking) without command
        if mode == "THINKING" and not command:
            if EventBusHandler.active_thinking_proc and EventBusHandler.active_thinking_proc.poll() is None:
                try:
                    EventBusHandler.active_thinking_proc.terminate()
                except Exception:
                    pass
            thinking_text = payload.get("text") or "[VOICE TRIGGER: LISTENING...]\nTranscribing voice command..."
            EventBusHandler.active_thinking_proc = spawn_hud_overlay(
                mode="THINKING",
                text=thinking_text,
                duration=max(duration, 4.0),
                cursor_pos=cursor_pos
            )
            self._send_json(200, {
                "status": "success",
                "mode": "THINKING",
                "message": "HUD Thinking (Listening) indicator active",
                "cursor_pos": cursor_pos
            })
            return

        audio_report = payload.get("audio_report")
        if not command and audio_report:
            dur = audio_report.get("duration_sec", 0.0)
            buf = audio_report.get("buffer_size_bytes", 0)
            command = f"echo [VOICE PTT] Duration: {dur:.2f}s, Buffer: {buf} bytes"

        if not command:
            self._send_json(400, {"status": "error", "message": "Missing 'command' field in payload"})
            return

        # If a thinking HUD is currently active from Step 2, close it when action finishes
        thinking_proc = EventBusHandler.active_thinking_proc
        EventBusHandler.active_thinking_proc = None

        if mode == "THINKING" and not thinking_proc:
            thinking_proc = spawn_hud_overlay(
                mode="THINKING",
                text=f"> {command}\n[STATUS: EXECUTING...]",
                duration=duration,
                cursor_pos=cursor_pos
            )

        # Execute terminal command
        result = self.actuator.execute_sync(command)

        if thinking_proc and thinking_proc.poll() is None:
            try:
                thinking_proc.terminate()
            except Exception:
                pass

        # Trigger HUD Overlay next to cursor:
        # Emerald ring (ACTION) for success or Red ring (ERROR) with error log details on failure
        hud_mode = "ERROR" if result.exit_code != 0 else "ACTION"
        stt_text = payload.get("stt_text")
        intent_id = payload.get("intent_id", "COMMAND")

        if stt_text:
            status_tag = f"[EXIT {result.exit_code}]" if result.exit_code == 0 else f"[ERR {result.exit_code}]"
            first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else (result.stderr.strip().splitlines()[0] if result.stderr.strip() else result.command)
            summary_text = (
                f"{status_tag} {intent_id}\n"
                f"Speech: \"{stt_text}\"\n"
                f"> {result.command}"
            )
        elif audio_report and result.exit_code == 0:
            dur = audio_report.get("duration_sec", 0.0)
            buf_kb = audio_report.get("buffer_size_bytes", 0) / 1024.0
            sr = audio_report.get("sample_rate", 16000)
            rms = audio_report.get("rms_energy", 0.0)
            summary_text = (
                f"[EXIT 0] Push-to-Talk Capture\n"
                f"Duration: {dur:.2f}s - Buffer: {buf_kb:.1f} KB\n"
                f"Format: {sr}Hz 16-bit Mono (RMS: {rms:.1f})"
            )
        else:
            summary_text = result.summary(max_lines=3)

        spawn_hud_overlay(
            mode=hud_mode,
            text=summary_text,
            duration=duration,
            cursor_pos=cursor_pos
        )

        response_payload = {
            "status": "success" if result.exit_code == 0 else "error",
            "command": result.command,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "duration_ms": result.duration_ms,
            "summary": summary_text,
            "cursor_pos": cursor_pos,
            "mode": hud_mode
        }
        self._send_json(200, response_payload)

    def log_message(self, format, *args):
        # Clean formatted logging to stdout with safe encoding
        try:
            msg = f"[EventBus] {self.address_string()} - {format % args}\n"
            sys.stdout.write(msg)
            sys.stdout.flush()
        except Exception:
            pass


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
    server_address = (host, port)
    while True:
        httpd = None
        try:
            httpd = ThreadingHTTPServer(server_address, EventBusHandler)
            print(f"[Windows Edge Event Bus] Server listening on http://{host}:{port}")
            print(f"  - POST http://{host}:{port}/action to trigger terminal task & cursor HUD")
            print(f"  - GET  http://{host}:{port}/health to check service status")
            sys.stdout.flush()
            httpd.serve_forever()
        except (KeyboardInterrupt, SystemExit):
            print("\n[Windows Edge Event Bus] Shutting down...")
            break
        except BaseException as e:
            try:
                import traceback
                traceback.print_exc()
            except Exception:
                pass
            time.sleep(0.5)
        finally:
            if httpd:
                try:
                    httpd.server_close()
                except Exception:
                    pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Windows Edge Local Event Bus")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST, help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port (default: 8765)")
    args = parser.parse_args()
    run_server(args.host, args.port)
