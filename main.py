import sys
import time

t_main_start = time.perf_counter()

# Configure unbuffered / line-buffered stdout and stderr for instantaneous terminal output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)

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
