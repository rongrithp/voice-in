import asyncio
import signal
import sys
import os
import json
import base64
import wave
from typing import Optional
from dotenv import load_dotenv
import sounddevice as sd
import numpy as np

load_dotenv()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

from src.fsm import VoiceFSM
from src.interception_controller import F20ToggleController
from src.keyboard_hook import GlobalKeyboardHook
from src.audio_buffer import AudioCaptureBuffer
from src.audio_provider import AudioCaptureProvider
from src.screen_capture import ScreenCaptureProvider
from src.transport import WebSocketTransport
from src.audio_player import AudioPlayer
from src.orchestrator import SystemOrchestrator

def get_jabra_devices():
    try:
        devices = sd.query_devices()
        mic_idx = None
        spk_idx = None
        for idx, dev in enumerate(devices):
            name = dev['name']
            hostapi = sd.query_hostapis(dev['hostapi'])['name']
            if "Windows DirectSound" in hostapi and "Jabra Link 390" in name:
                if dev['max_input_channels'] > 0 and mic_idx is None:
                    mic_idx = idx
                if dev['max_output_channels'] > 0 and spk_idx is None:
                    spk_idx = idx
        return mic_idx, spk_idx
    except Exception as e:
        print(f"[WARN] Failed to auto-discover Jabra devices: {e}", flush=True)
        return None, None

def build_system(
    ws_endpoint: str = "ws://localhost:8080/stream",
    mic_device: Optional[int] = None,
    speaker_device: Optional[int] = None,
) -> SystemOrchestrator:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    fsm = VoiceFSM()

    disc_mic, disc_spk = get_jabra_devices()
    default_in = disc_mic if disc_mic is not None else int(os.getenv("AUDIO_INPUT_DEVICE_ID", "8"))
    default_out = disc_spk if disc_spk is not None else int(os.getenv("AUDIO_OUTPUT_DEVICE_ID", "11"))

    in_dev = default_in if mic_device is None else mic_device
    out_dev = default_out if speaker_device is None else speaker_device
    print(f"[CONFIG] Mic Device Index: {in_dev} (Jabra DirectSound), Speaker Device Index: {out_dev} (Jabra DirectSound)", flush=True)

    audio_buffer = AudioCaptureBuffer(max_chunks=200)
    audio_provider = AudioCaptureProvider(buffer=audio_buffer, loop=loop, device=in_dev, persistent=True)
    screen_capture = None  # Muted: Isolate to voice-only (no image frames)
    audio_player = AudioPlayer(samplerate=24000, channels=2, output_device_index=out_dev)
    audio_player.start()

    transport = WebSocketTransport(
        endpoint=ws_endpoint,
        on_message=None,  # Configured by orchestrator
        on_error=lambda err: asyncio.sleep(0),
        on_close=lambda: asyncio.sleep(0)
    )

    orchestrator = SystemOrchestrator(
        fsm=fsm,
        keyboard_hook=None,
        audio_provider=audio_provider,
        audio_buffer=audio_buffer,
        screen_capture=screen_capture,
        transport=transport,
        audio_player=audio_player,
        loop=loop
    )

    keyboard_hook = GlobalKeyboardHook(
        target_key="f20",
        on_press_coro=orchestrator.handle_f20_press,
        on_release_coro=orchestrator.handle_f20_release,
        loop=loop
    )
    orchestrator._keyboard_hook = keyboard_hook

    # Wire incoming message handler to orchestrator
    transport._on_message = orchestrator._handle_transport_message
    return orchestrator

async def ws_receiver_loop(transport, target_or_player, audio_provider=None, device=None):
    if hasattr(target_or_player, "audio_player"):
        audio_player = target_or_player.audio_player
        audio_provider = getattr(target_or_player, "audio_provider", audio_provider)
    else:
        audio_player = target_or_player

    out_dev = device if device is not None else getattr(audio_player, "_device", int(os.getenv("AUDIO_OUTPUT_DEVICE_ID", "13")))
    received_pcm_bytes = bytearray()
    print("[SYSTEM] >>> Receiver Loop STARTED successfully.", flush=True)

    try:
        from unittest.mock import MagicMock, AsyncMock
        ws = getattr(transport, "websocket", getattr(transport, "_ws", None))
        if ws is None or isinstance(ws, (MagicMock, AsyncMock)):
            return
        async for raw_msg in ws:
            print(f"[WS RAW RECV] Message length: {len(raw_msg)}", flush=True)
            try:
                msg = json.loads(raw_msg)
            except Exception:
                continue
            if "serverContent" in msg:
                sc = msg["serverContent"]
                if "modelTurn" in sc:
                    for part in sc["modelTurn"].get("parts", []):
                        if "inlineData" in part:
                            data = base64.b64decode(part["inlineData"]["data"])
                            print(f"[RECV] 🔊 Audio chunk from Gemini: {len(data)} bytes", flush=True)
                            received_pcm_bytes.extend(data)
                            if audio_player:
                                audio_player.play(data)
                if sc.get("turnComplete"):
                    print(f"[RECV] ✅ Gemini turnComplete received! Accumulated {len(received_pcm_bytes)} bytes", flush=True)
                    if len(received_pcm_bytes) > 0:
                        # 1. สั่ง mic_stream.stop() ชั่วคราวเพื่อปล่อย Hardware Lock ของ Jabra
                        mic_stream = getattr(audio_provider, "_stream", None) if audio_provider else None
                        if mic_stream:
                            try:
                                mic_stream.stop()
                                print("[HARDWARE] 🎤 Mic stream stopped to release Jabra hardware lock", flush=True)
                            except Exception:
                                pass

                        # 2. เขียนข้อมูลลงไฟล์ชั่วคราว response.wav (ความถี่ 24000Hz, 1ch, 16-bit)
                        wav_filename = "response.wav"
                        try:
                            with wave.open(wav_filename, "wb") as wf:
                                wf.setnchannels(1)
                                wf.setsampwidth(2)
                                wf.setframerate(24000)
                                wf.writeframes(bytes(received_pcm_bytes))
                            print(f"[WAV] ✅ Saved {len(received_pcm_bytes)} bytes to {wav_filename}", flush=True)
                        except Exception as e:
                            print(f"[WAV ERROR] Failed to save {wav_filename}: {e}", flush=True)

                        # 3. สั่งเล่นผ่าน sd.play(audio_data, samplerate=24000, device=13, blocking=True)
                        try:
                            audio_np = np.frombuffer(bytes(received_pcm_bytes), dtype=np.int16)
                            print(f"[PLAYBACK] 🔊 Playing {len(audio_np)} samples on Device {out_dev} (blocking)...", flush=True)
                            loop = asyncio.get_running_loop()
                            await loop.run_in_executor(
                                None,
                                lambda: sd.play(audio_np, samplerate=24000, device=out_dev, blocking=True)
                            )
                            print("[PLAYBACK] ✅ Playback finished successfully!", flush=True)
                        except Exception as e:
                            print(f"[PLAYBACK ERROR] sd.play failed: {e}", flush=True)

                        # 4. เมื่อเล่นจบ จึงค่อยสั่ง mic_stream.start() กลับมารอฟังรอบใหม่
                        if mic_stream:
                            try:
                                mic_stream.start()
                                print("[HARDWARE] 🎤 Mic stream restored", flush=True)
                            except Exception:
                                pass

                        # Clear buffer for next turn
                        received_pcm_bytes = bytearray()

    except Exception as e:
        import traceback
        print(f"[ERROR] WebSocket Receiver CRASHED: {e}", flush=True)
        traceback.print_exc()

async def run_app(stop_event: Optional[asyncio.Event] = None) -> None:
    api_key = os.getenv("GEMINI_API_KEY", "")
    default_endpoint = (
        f"wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?key={api_key}"
        if api_key else "ws://localhost:8080/stream"
    )
    endpoint = os.getenv("VOICE_WS_ENDPOINT", default_endpoint)
    orchestrator = build_system(ws_endpoint=endpoint)

    started = await orchestrator.start()
    if not started:
        print("[ERROR] Failed to establish upstream transport connection.")
        return

    print("[SYSTEM READY] Press F20 to capture/interact. Ctrl+C to exit.", flush=True)

    # Cancel internal _receive_task so ws_receiver_loop is the single consumer on transport.websocket
    receive_task = None
    if isinstance(orchestrator.transport, WebSocketTransport):
        if orchestrator.transport._receive_task:
            orchestrator.transport._receive_task.cancel()
            orchestrator.transport._receive_task = None
        receive_task = asyncio.create_task(ws_receiver_loop(orchestrator.transport, orchestrator))

    if stop_event is None:
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                # Windows fallback for signals
                pass

    try:
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        print("\n[SYSTEM] Shutting down cleanly...")
        if receive_task and not receive_task.done():
            receive_task.cancel()
        await orchestrator.shutdown()
        print("[SYSTEM] All streams closed. Exited.")

if __name__ == "__main__":
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        sys.exit(0)
