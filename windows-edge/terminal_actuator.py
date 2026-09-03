"""
terminal_actuator.py - Cursor-Context Terminal Runner & HUD Actuator
Windows Edge Module: Non-blocking subprocess execution with direct HUD piping.
"""

import sys
import os
import time
import subprocess
import threading
import argparse
from typing import Optional, Callable, Dict, Any, Tuple


class ActuatorResult:
    """Represents the execution outcome of a terminal command."""

    def __init__(
        self,
        command: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        duration_ms: float
    ):
        self.command = command
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms

    @property
    def is_success(self) -> bool:
        return self.exit_code == 0

    def summary(self, max_lines: int = 4, max_chars_per_line: int = 60) -> str:
        """Returns a condensed, clean summary suitable for cognitive HUD display."""
        status_tag = f"[EXIT {self.exit_code}] ({self.duration_ms:.0f}ms)"
        raw_output = (self.stdout if self.is_success or not self.stderr.strip() else self.stderr).strip()

        lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
        if not lines:
            return f"{status_tag} {self.command}"

        preview_lines = []
        for line in lines[-max_lines:]:
            if len(line) > max_chars_per_line:
                line = line[:max_chars_per_line - 3] + "..."
            preview_lines.append(line)

        return f"{status_tag} {self.command}\n" + "\n".join(preview_lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "is_success": self.is_success
        }


def spawn_hud_overlay(
    mode: str = "ACTION",
    text: Optional[str] = None,
    duration: float = 2.5,
    cursor_pos: Optional[Tuple[int, int]] = None
) -> Optional[subprocess.Popen]:
    """
    Spawns hud_overlay.py in a detached non-blocking process so caller thread never stalls.
    Supports passing optional cursor coordinates (x, y) or autodetecting in HUD process.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    hud_script = os.path.join(script_dir, "hud_overlay.py")

    cmd = [
        sys.executable,
        hud_script,
        "--mode", mode,
        "--duration", str(duration)
    ]
    if text:
        cmd.extend(["--text", text])
    if cursor_pos is not None:
        cmd.extend(["--x", str(cursor_pos[0]), "--y", str(cursor_pos[1])])

    try:
        # Launch without creating a visible console window (DETACHED_PROCESS / CREATE_NO_WINDOW)
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            close_fds=True
        )
        return proc
    except Exception as e:
        sys.stderr.write(f"[Actuator] Failed to spawn HUD overlay: {e}\n")
        return None


class TerminalActuator:
    """
    Non-blocking terminal task runner that pipes outcomes directly to the HUD overlay.
    """

    def __init__(self, default_hud_duration: float = 2.5):
        self.default_hud_duration = default_hud_duration

    def execute_sync(self, command: str, timeout: Optional[float] = 60.0) -> ActuatorResult:
        """
        Synchronously runs a terminal task and captures exit code, stdout, and stderr.
        """
        start_time = time.perf_counter()
        try:
            # Use shell=True for native Windows command resolution (dir, echo, PowerShell etc.)
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            stdout, stderr = proc.communicate(timeout=timeout)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            exit_code = -1
            stderr = f"{stderr}\n[Actuator Error] Command timed out after {timeout}s"
        except Exception as ex:
            stdout = ""
            stderr = f"[Actuator Exception] {ex}"
            exit_code = -2

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        return ActuatorResult(
            command=command,
            exit_code=exit_code,
            stdout=stdout or "",
            stderr=stderr or "",
            duration_ms=duration_ms
        )

    def execute_and_display(
        self,
        command: str,
        duration: Optional[float] = None,
        timeout: Optional[float] = 60.0
    ) -> ActuatorResult:
        """
        Synchronously runs a command and pops up the outcome HUD immediately next to the cursor.
        """
        hud_dur = duration if duration is not None else self.default_hud_duration
        result = self.execute_sync(command, timeout=timeout)
        spawn_hud_overlay(
            mode="ACTION",
            text=result.summary(max_lines=3),
            duration=hud_dur
        )
        return result

    def execute_async(
        self,
        command: str,
        on_complete: Optional[Callable[[ActuatorResult], None]] = None,
        show_hud: bool = True,
        show_thinking_hud: bool = True,
        hud_duration: Optional[float] = None
    ) -> threading.Thread:
        """
        Executes a terminal task asynchronously in a background thread.
        Pipes THINKING status at invocation and ACTION status outcome upon completion.
        """
        duration = hud_duration if hud_duration is not None else self.default_hud_duration

        def _worker():
            # 1. Immediate Cognitive Anchoring HUD (THINKING mode)
            thinking_proc = None
            if show_hud and show_thinking_hud:
                thinking_text = f"> {command}\n[STATUS: EXECUTING...]"
                thinking_proc = spawn_hud_overlay(
                    mode="THINKING",
                    text=thinking_text,
                    duration=duration
                )

            # 2. Execute command
            result = self.execute_sync(command)

            # Terminate thinking HUD if it's still alive
            if thinking_proc and thinking_proc.poll() is None:
                try:
                    thinking_proc.terminate()
                except Exception:
                    pass

            # 3. Pipe status outcome immediately into ACTION mode HUD
            if show_hud:
                summary_text = result.summary(max_lines=3)
                spawn_hud_overlay(
                    mode="ACTION",
                    text=summary_text,
                    duration=duration
                )

            # 4. Notify completion callback if registered
            if on_complete:
                try:
                    on_complete(result)
                except Exception as err:
                    sys.stderr.write(f"[Actuator] Callback exception: {err}\n")

        thread = threading.Thread(target=_worker, daemon=True, name=f"ActuatorThread-{time.time()}")
        thread.start()
        return thread


def main():
    parser = argparse.ArgumentParser(description="Windows Edge - Terminal Actuator & HUD Runner")
    parser.add_argument("--cmd", type=str, default="echo Windows Edge Terminal Actuator Online", help="Command to execute")
    parser.add_argument("--no-hud", action="store_true", help="Disable HUD overlay rendering")
    parser.add_argument("--duration", type=float, default=2.5, help="HUD display duration in seconds")
    parser.add_argument("--test-mode", action="store_true", help="Run automated self-test and exit")
    args = parser.parse_args()

    actuator = TerminalActuator(default_hud_duration=0.4 if args.test_mode else args.duration)

    if args.test_mode:
        print("[TerminalActuator] Running automated self-test...")
        result = actuator.execute_sync("echo Actuator Verification OK")
        assert result.exit_code == 0, f"Expected exit code 0, got {result.exit_code}"
        assert "Actuator Verification OK" in result.stdout, f"Unexpected stdout: {result.stdout}"
        print(f"[TerminalActuator] Success: {result.summary()}")

        # Test HUD pipe
        proc = spawn_hud_overlay(mode="ACTION", text="[TEST OK] Actuator Verified", duration=0.3)
        if proc:
            proc.wait(timeout=3.0)
        print("[TerminalActuator] Self-test complete with 0 errors.")
        sys.exit(0)

    # Standard execution
    print(f"[Actuator] Dispatching command: {args.cmd}")
    done_event = threading.Event()

    def _on_done(res: ActuatorResult):
        print(f"\n[Actuator Completed] Exit Code: {res.exit_code} ({res.duration_ms:.1f}ms)")
        print(f"Stdout:\n{res.stdout.strip()}")
        if res.stderr.strip():
            print(f"Stderr:\n{res.stderr.strip()}")
        done_event.set()

    actuator.execute_async(
        command=args.cmd,
        on_complete=_on_done,
        show_hud=not args.no_hud,
        hud_duration=args.duration
    )

    done_event.wait(timeout=30.0)


if __name__ == "__main__":
    main()
