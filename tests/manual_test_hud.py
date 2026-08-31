import time
import sys
import os

# Add root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hud_overlay import HUDOverlay, HUDState

def main():
    print("[Manual HUD Test] Starting HUD Overlay Test on Monitor 1 (UltraWide 3440x1440)...")
    hud = HUDOverlay(position="top-center")
    hud.start()
    time.sleep(0.5)

    print("0. Showing System Booting State (Yellow)...")
    hud.show_system_booting()
    time.sleep(1.5)

    print("0.1 Showing System Ready State (Green with 2.5s auto-hide)...")
    hud.show_system_ready(auto_hide_seconds=2.5)
    time.sleep(3.0)

    print("1. Showing STT Connecting State (Yellow)...")
    hud.show_stt_connecting()
    time.sleep(1.5)

    print("2. Showing STT Active Streaming State (Red with VU Meter)...")
    hud.show_stt()
    for rms_val in [200.0, 800.0, 2200.0, 3500.0, 500.0, 100.0]:
        hud.update_audio_level(rms_val)
        time.sleep(0.3)

    print("3. Showing STT Finalizing (White)...")
    hud.show_stt_finalizing()
    time.sleep(1.2)

    print("4. Showing Gemini Live Phase 1: [1/2] Connecting Server (Amber)...")
    hud.show_live_connecting()
    time.sleep(1.5)

    print("5. Showing Gemini Live Phase 2: [2/2] Initializing Model & Memory (Amber/Orange)...")
    hud.show_live_handshake()
    time.sleep(1.5)

    print("6. Showing Gemini Live Phase 3: Connected & Ready (Green Emerald with VU)...")
    hud.show_live()
    for rms_val in [300.0, 1200.0, 2800.0, 4000.0, 150.0]:
        hud.update_audio_level(rms_val)
        time.sleep(0.3)
    time.sleep(1.0)

    print("7. Showing Gemini Live Error Boundary (Red Alert)...")
    hud.show_live_error(auto_hide_seconds=2.0)
    time.sleep(2.5)

    print("8. Showing TTS Reading State (Cyan)...")
    hud.show_tts()
    time.sleep(1.5)

    print("9. Hiding HUD...")
    hud.hide()
    time.sleep(1)

    print("10. Stopping HUD Overlay...")
    hud.stop()
    print("[Manual HUD Test] Completed successfully 100%.")

if __name__ == "__main__":
    main()
