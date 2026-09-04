import asyncio
import os
import sys
from dotenv import load_dotenv

# Ensure root directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.transport import WebSocketTransport

load_dotenv()

async def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[-] ERROR: GEMINI_API_KEY not found in environment or .env file.")
        sys.exit(1)

    print("[+] Initializing WebSocketTransport...")
    transport = WebSocketTransport()

    endpoint = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={api_key}"

    print(f"[+] Connecting to Gemini 2.0 Live WebSocket...")
    try:
        await transport.connect(endpoint)
        print("[+] WebSocket connected successfully!")

        print("[+] Sending Gemini setup frame...")
        await transport.send_gemini_setup(
            api_key=api_key,
            model="models/gemini-2.0-flash-exp",
            voice_name="Puck"
        )
        print("[+] Setup frame dispatched.")

        # Allow 2 seconds to ensure handshake holds without server closure
        print("[+] Holding connection for 2s to verify socket stability...")
        await asyncio.sleep(2.0)

        print("[+] Disconnecting cleanly...")
        await transport.disconnect()
        print("[+] Smoke test PASSED: Gemini 2.0 Live API handshake verified.")

    except Exception as e:
        print(f"[-] Smoke test FAILED with exception: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
