import time
import sys
import os

# Add root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.hud_overlay import HUDOverlay, HUDState

def main():
    print("[Manual HUD Test] Starting HUD Overlay Test...")
    hud = HUDOverlay(position="top-center")
    hud.start()
    time.sleep(0.5)

    print("1. Showing STT Connecting State (Yellow)...")
    hud.show_stt_connecting()
    time.sleep(2)

    print("2. Showing STT Active Streaming State (Red with VU Meter)...")
    hud.show_stt()
    for rms_val in [200.0, 800.0, 2200.0, 3500.0, 500.0, 100.0]:
        hud.update_audio_level(rms_val)
        time.sleep(0.4)

    print("3. Showing STT Finalizing (White)...")
    hud.show_stt_finalizing()
    time.sleep(1.5)

    print("4. Showing Gemini Live Connecting State (Orange/Yellow)...")
    hud.show_live_connecting()
    time.sleep(2)

    print("5. Showing Gemini Live Connected State (Green)...")
    hud.show_live()
    time.sleep(2)

    print("6. Showing Gemini Live Closing State (Grey)...")
    hud.show_live_closing()
    time.sleep(1.5)

    print("7. Showing TTS Reading State (Blue)...")
    hud.show_tts()
    time.sleep(2)

    print("8. Hiding HUD...")
    hud.hide()
    time.sleep(1)

    print("9. Stopping HUD Overlay...")
    hud.stop()
    print("[Manual HUD Test] Completed successfully.")

if __name__ == "__main__":
    main()
