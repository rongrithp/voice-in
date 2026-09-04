import sounddevice as sd
import numpy as np

print("=== AUDIO DEVICES ===")
print(sd.query_devices())
print("\n=== DEFAULT HOST API & DEVICES ===")
print(sd.default.device)

print("\n=== TESTING MIC INPUT LEVEL (2 SECONDS) ===")
duration = 2.0  # seconds
sample_rate = 16000
recording = sd.rec(int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype='int16')
sd.wait()

max_amplitude = np.max(np.abs(recording))
rms = np.sqrt(np.mean(recording.astype(np.float32)**2))
print(f"[+] Peak Amplitude: {max_amplitude} / 32767")
print(f"[+] RMS Energy: {rms:.2f}")

if max_amplitude < 100:
    print("[-] WARNING: Microphone captured near-zero signal (Muted or wrong device).")
else:
    print("[+] Microphone is capturing physical audio successfully.")
