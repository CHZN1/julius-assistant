"""Echo self-test: plays recorded Julius speech through a DUPLEX stream (microphone and speaker on one device clock) and measures how well echo cancellation works
when the reference is sample-aligned. Nobody should talk while it runs (~75 s). Usage: python diagnostics/aec_selftest.py [out_dir]

Phases: 4 s silence (noise floor), then ~50 s of Julius's voice from an earlier recording. The reference is the exact block handed to the speaker in each callback,
so the only delay left is the device round trip, which the canceller (and this script's lag estimate) deals with."""
import sys, time, wave, json, glob
from pathlib import Path
import numpy as np, sounddevice as sd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RATE, BLOCK = 16000, 320


def stimulus(seconds=50):
    """Speech-like Julius audio: the loudest stretches of recorded playback."""
    chunks = []
    for f in sorted(glob.glob(str(ROOT / "voice_data" / "*" / "*__take*" / "rec" / "out.wav"))):
        with wave.open(f) as w: x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32767
        win = RATE // 2
        for i in range(0, len(x) - win, win):
            if np.sqrt(np.mean(x[i:i + win] ** 2)) > 0.03: chunks.append(x[i:i + win])
        if sum(len(c) for c in chunks) > seconds * RATE: break
    sig = np.concatenate(chunks)[: seconds * RATE]
    return sig


def main():
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/aec_selftest"); out_dir.mkdir(parents=True, exist_ok=True)
    sig = stimulus(); sil = np.zeros(4 * RATE, dtype=np.float32); play = np.concatenate([sil, sig, np.zeros(3 * RATE, dtype=np.float32)])
    mic_log, ref_log, status_log = [], [], []; pos = [0]
    def callback(indata, outdata, frames, tinfo, status):
        i = pos[0]; block = play[i:i + frames]
        outdata[:, 0] = 0; outdata[:len(block), 0] = block; pos[0] += frames
        mic_log.append(indata[:, 0].copy()); ref_log.append(outdata[:, 0].copy())
        if status: status_log.append(str(status))
        if pos[0] >= len(play): raise sd.CallbackStop
    dev = sd.query_devices(kind="input")["name"], sd.query_devices(kind="output")["name"]
    print("devices:", dev, flush=True)
    with sd.Stream(samplerate=RATE, blocksize=BLOCK, channels=1, dtype="float32", latency="low", callback=callback) as st:
        print(f"duplex stream open, latency in/out {st.latency}; playing for {len(play) / RATE:.0f} s, stay quiet...", flush=True)
        while st.active: time.sleep(0.2)
    mic = np.concatenate(mic_log); ref = np.concatenate(ref_log)
    for name, x in (("mic", mic), ("ref", ref)):
        with wave.open(str(out_dir / f"{name}.wav"), "wb") as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    (out_dir / "info.json").write_text(json.dumps({"latency": st.latency, "status": status_log[:5], "devices": dev}))
    print("saved", out_dir, "status flags:", len(status_log), flush=True)


if __name__ == "__main__":
    main()
