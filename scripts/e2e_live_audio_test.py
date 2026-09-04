import asyncio
import os
import sys
import sounddevice as sd
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.transport import WebSocketTransport
from src.audio_player import AudioPlayer

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

async def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("[-] ERROR: GEMINI_API_KEY not set.")
        sys.exit(1)

    # DirectSound Drivers for Jabra Link 390
    MIC_DEVICE_ID = 9       # Headset Microphone (2- Jabra Link 390), Windows DirectSound
    SPEAKER_DEVICE_ID = 13   # Headset Earphone (2- Jabra Link 390), Windows DirectSound

    transport = WebSocketTransport()
    player = AudioPlayer(samplerate=24000, output_device_index=SPEAKER_DEVICE_ID)
    player.start()

    received_chunks = 0
    total_bytes = 0

    def on_audio(pcm_data):
        nonlocal received_chunks, total_bytes
        received_chunks += 1
        total_bytes += len(pcm_data)
        if received_chunks == 1:
            print("\n[🔊 AUDIO STREAM] เริ่มได้รับข้อมูลเสียงตอบกลับจาก Gemini...")
        player.play_chunk(pcm_data)

    transport.set_audio_callback(on_audio)
    transport.set_interrupted_callback(lambda: player.stop())

    endpoint = f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={api_key}"

    print("[+] Connecting to Gemini 2.0 Live...")
    await transport.connect(endpoint)
    await transport.send_gemini_setup(
        api_key=api_key,
        model="models/gemini-2.5-flash-native-audio-latest",
        voice_name="Puck"
    )
    print("[+] Gemini Live session armed and ready.")

    loop = asyncio.get_running_loop()
    audio_queue = asyncio.Queue()

    def mic_callback(indata, frames, time_info, status):
        if status:
            print(f"[!] Mic status: {status}", file=sys.stderr)
        loop.call_soon_threadsafe(audio_queue.put_nowait, bytes(indata))

    # Open microphone: 16kHz, mono, 16-bit PCM
    mic_stream = sd.RawInputStream(
        samplerate=16000,
        channels=1,
        dtype='int16',
        blocksize=1024,
        device=MIC_DEVICE_ID,
        callback=mic_callback
    )

    print("\n" + "="*50)
    print("🎙️  RECORDING... พูดใส่ไมโครโฟนได้เลย (เช่น 'สวัสดี แนะนำตัวสั้นๆ หน่อย') [จับเสียง 4 วินาที]")
    print("="*50)

    mic_stream.start()
    
    # Task to pump audio from queue to transport
    async def pump_mic_audio():
        while mic_stream.active:
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=0.1)
                await transport.send_audio_chunk(chunk)
            except asyncio.TimeoutError:
                continue

    pump_task = asyncio.create_task(pump_mic_audio())

    await asyncio.sleep(4.0)

    mic_stream.stop()
    mic_stream.close()
    pump_task.cancel()
    await transport.send_audio_stream_end()
    print("\n[+] Mic closed. ส่ง audioStreamEnd แล้ว กำลังรอรับเสียงตอบกลับจาก Gemini...")

    # Wait up to 7 seconds to let Gemini respond and finish playing audio
    await asyncio.sleep(7.0)

    print(f"[+] Total audio chunks received: {received_chunks} ({total_bytes} bytes)")
    print("[+] Cleaning up...")
    player.stop()
    await transport.disconnect()
    print("[+] End-to-End Live Audio Test Completed cleanly.")

if __name__ == "__main__":
    asyncio.run(main())
