import sys
import time

t_main_start = time.perf_counter()

# Ensure stdout/stderr are valid streams under pythonw.exe (windowless background mode)
class _SafeStreamWriter:
    def write(self, text):
        pass
    def flush(self):
        pass

if sys.stdout is None:
    sys.stdout = _SafeStreamWriter()
elif hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

if sys.stderr is None:
    sys.stderr = _SafeStreamWriter()
elif hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

print("[Startup] Initializing Voice Operating Hub Daemon...", flush=True)

t_import_start = time.perf_counter()
from src.app import VoiceOperatingHubApp
import_ms = (time.perf_counter() - t_import_start) * 1000
print(f"[Startup Benchmark] Application modules imported in {import_ms:.1f}ms", flush=True)

if __name__ == "__main__":
    t_app_init = time.perf_counter()
    print("[Startup] Instantiating VoiceOperatingHubApp...", flush=True)
    try:
        app = VoiceOperatingHubApp()
        init_ms = (time.perf_counter() - t_app_init) * 1000
        print(f"[Startup Benchmark] VoiceOperatingHubApp instantiated in {init_ms:.1f}ms", flush=True)

        print("[Startup] Starting daemon main loop...", flush=True)
        app.run()
    except KeyboardInterrupt:
        print("\n[Shutdown] Voice Operating Hub stopped by user.", flush=True)
        sys.exit(0)
    except Exception as e:
        print(f"[Fatal Error] Daemon terminated unexpectedly: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
