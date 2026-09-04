import asyncio
import os
import sys
import json
import base64
import sounddevice as sd
import websockets
from dotenv import load_dotenv

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

API_KEY = os.getenv("GEMINI_API_KEY")
HOST = "generativelanguage.googleapis.com"
URL = f"wss://{HOST}/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={API_KEY}"

MIC_DEVICE_ID = 9     # DirectSound Jabra Mic
SPEAKER_DEVICE_ID = 13 # DirectSound Jabra Speaker

async def run():
    print(f"[+] Connecting to {URL[:70]}...")
    async with websockets.connect(URL) as ws:
        print("[+] WebSocket Opened.")

        # 1. Send Setup Frame
        setup_payload = {
            "setup": {
                "model": "models/gemini-2.5-flash-native-audio-latest",
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": "Aoede"
                            }
                        }
                    }
                }
            }
        }
        await ws.send(json.dumps(setup_payload))
        print("[+] Sent Setup Frame.")

        # 2. Wait for setupComplete from server
        first_resp = await ws.recv()
        print(f"[+] First Server Response: {first_resp}")

        # 3. Start audio capture & playback stream
        player_stream = sd.RawOutputStream(
            samplerate=24000,
            channels=1,
            dtype='int16',
            device=SPEAKER_DEVICE_ID
        )
        player_stream.start()

        audio_queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def mic_callback(indata, frames, time_info, status):
            loop.call_soon_threadsafe(audio_queue.put_nowait, bytes(indata))

        mic_stream = sd.RawInputStream(
            samplerate=16000,
            channels=1,
            dtype='int16',
            blocksize=1024,
            device=MIC_DEVICE_ID,
            callback=mic_callback
        )
        mic_stream.start()

        print("\n" + "="*50)
        print("🎙️ RECORDING... พูดใส่ไมค์ Jabra สั้นๆ ได้เลย (3 วินาที)")
        print("="*50)

        async def send_audio():
            sent_count = 0
            try:
                while mic_stream.active:
                    try:
                        chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
                        b64_data = base64.b64encode(chunk).decode("utf-8")
                        msg = {
                            "realtime_input": {
                                "mediaChunks": [
                                    {
                                        "mimeType": "audio/pcm;rate=16000",
                                        "data": b64_data
                                    }
                                ]
                            }
                        }
                        await ws.send(json.dumps(msg))
                        sent_count += 1
                        if sent_count % 10 == 0:
                            print(f"[🎤 MIC] Sent {sent_count} chunks to Gemini...")
                    except asyncio.TimeoutError:
                        continue
            except Exception as e:
                print(f"[!] Error in send_audio: {e}", file=sys.stderr)

        async def receive_server():
            try:
                async for raw_msg in ws:
                    if isinstance(raw_msg, bytes):
                        raw_msg = raw_msg.decode("utf-8", errors="replace")
                    print(f"[📥 SERVER]: {raw_msg[:160]}...")
                    data = json.loads(raw_msg)
                    server_content = data.get("serverContent", {})
                    model_turn = server_content.get("modelTurn", {})
                    parts = model_turn.get("parts", [])
                    for part in parts:
                        inline_data = part.get("inlineData", {})
                        if "data" in inline_data:
                            raw_pcm = base64.b64decode(inline_data["data"])
                            print(f"[🔊 AUDIO] Received {len(raw_pcm)} bytes PCM")
                            player_stream.write(raw_pcm)

                    if "interrupted" in server_content:
                        print("[!] Interrupted by server!")

                    if server_content.get("turnComplete"):
                        print("[+] Server Turn Complete.")
            except Exception as e:
                print(f"[!] Error in receive_server: {e}", file=sys.stderr)

        send_task = asyncio.create_task(send_audio())
        recv_task = asyncio.create_task(receive_server())

        await asyncio.sleep(4.0)

        # Stop Mic & Send audioStreamEnd
        mic_stream.stop()
        mic_stream.close()
        send_task.cancel()
        print("\n[+] Mic closed. Sending audioStreamEnd...")

        stream_end_msg = {
            "realtime_input": {
                "audioStreamEnd": True
            }
        }
        await ws.send(json.dumps(stream_end_msg))

        print("[+] Waiting 6s for response playback...")
        await asyncio.sleep(6.0)

        recv_task.cancel()
        player_stream.stop()
        player_stream.close()
        print("[+] Test finished.")

if __name__ == "__main__":
    asyncio.run(run())
