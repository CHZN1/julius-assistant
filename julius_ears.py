# julius_ears.py
import numpy as np, sounddevice as sd, mlx_whisper, time
from collections import deque

SR          = 16000
CHUNK       = 1024
SILENCE_TH  = 0.015      # tune to your room
SILENCE_LEN = 1.0        # seconds of quiet that ends an utterance
MIN_SPEECH  = 0.4        # ignore blips shorter than this
MODEL       = "mlx-community/whisper-large-v3-turbo"

buf, recording, silence_start, speech_start = [], False, None, None
q = deque()

def cb(indata, frames, t, status):
    q.append(indata[:,0].copy())

print("calibrating room noise — stay quiet 2s...")
with sd.InputStream(samplerate=SR, channels=1, blocksize=CHUNK, callback=cb):
    time.sleep(2)
noise = np.concatenate(list(q)) if q else np.zeros(SR)
floor = float(np.abs(noise).mean())
SILENCE_TH = max(SILENCE_TH, floor*4)
q.clear()
print(f"noise floor {floor:.4f} -> threshold {SILENCE_TH:.4f}")
print("listening — Ctrl+C to stop\n")

with sd.InputStream(samplerate=SR, channels=1, blocksize=CHUNK, callback=cb):
    try:
        while True:
            if not q:
                time.sleep(0.01); continue
            chunk = q.popleft()
            level = float(np.abs(chunk).mean())
            now = time.time()

            if level > SILENCE_TH:
                if not recording:
                    recording, speech_start = True, now
                    print("  [speaking...]", end="", flush=True)
                silence_start = None
                buf.append(chunk)
            elif recording:
                buf.append(chunk)
                if silence_start is None:
                    silence_start = now
                elif now - silence_start > SILENCE_LEN:
                    dur = now - speech_start
                    audio = np.concatenate(buf)
                    buf, recording, silence_start = [], False, None
                    if dur < MIN_SPEECH:
                        print(" (too short)"); continue
                    print(" transcribing...", end="", flush=True)
                    t0 = time.time()
                    r = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL)
                    txt = r["text"].strip()
                    lang = r.get("language","?")
                    print(f" [{time.time()-t0:.1f}s, {lang}]")
                    if txt: print(f"  YOU: {txt}\n")
    except KeyboardInterrupt:
        print("\nstopped")