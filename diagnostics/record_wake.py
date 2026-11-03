"""Record wake-word test audio for the wake-word eval (16 kHz mono). Files go to diagnostics/wake_real/<session>/ (local only; *.wav is gitignored).

  python diagnostics/record_wake.py wake    30 wake utterances ("Julius", "Hey Julius", with a request), about 5 minutes
  python diagnostics/record_wake.py wake2   the same 30 prompts saved as wake2 (a fresh eval set); wake3 ... likewise
  python diagnostics/record_wake.py talk1   about 35 ordinary sentences that must NOT wake Julius (also talk2, talk3); about 5 minutes each
Each prompt waits for you to start speaking and stops after 0.8 s of silence; rerun to resume where you stopped.
"""
import json, queue, sys, time
from pathlib import Path
import numpy as np, sounddevice as sd, soundfile as sf
ROOT = Path(__file__).resolve().parent.parent
SR, CHUNK = 16000, 1024
session = sys.argv[1] if len(sys.argv) > 1 else ""
prompts = json.loads((ROOT / "diagnostics" / "wake_prompts.json").read_text())
key = session if session in prompts else session.rstrip("0123456789")  # wake2, wake3 ... reuse the wake prompts under a new name
if key not in prompts: sys.exit(f"usage: record_wake.py {'|'.join(prompts)}")
items = [(p, "") if isinstance(p, str) else tuple(p) for p in prompts[key]]
out = ROOT / "diagnostics" / "wake_real" / session; out.mkdir(parents=True, exist_ok=True)
(out / "prompts.json").write_text(json.dumps(items, indent=1))
q = queue.Queue()
def cb(indata, frames, t, status): q.put(indata[:, 0].copy())
def listen(seconds):
    chunks = []; end = time.time() + seconds
    while time.time() < end: chunks.append(q.get())
    return np.concatenate(chunks)
with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=CHUNK, callback=cb):
    print("Calibrating room noise: stay quiet for 1.5 s ...", flush=True)
    floor = float(np.abs(listen(1.5)).mean()); threshold = min(0.02, max(0.0045, floor * 2.2))
    print(f"threshold {threshold:.4f}. Say each line when it appears (as you would to a person, not read like a list); wait for the check mark.\n", flush=True)
    i = 0
    while i < len(items) and (out / f"{session}_{i+1:02d}.wav").exists(): i += 1
    if i: print(f"Resuming at {i+1}; the first {i} are already recorded.\n", flush=True)
    while i < len(items):
        while not q.empty(): q.get()
        text, note = items[i]
        print(f"[{i+1}/{len(items)}] SAY:  {text}" + (f"   ({note})" if note else ""), flush=True)
        buf, recording, silent_since, started, pre = [], False, None, time.time(), []
        while True:
            chunk = q.get(); level = float(np.abs(chunk).mean()); now = time.time()
            if not recording:
                pre = (pre + [chunk])[-8:]
                if level > threshold: recording, buf = True, list(pre)
                elif now - started > 20: print("   (no speech heard, asking again)", flush=True); break
            else:
                buf.append(chunk)
                if level > threshold: silent_since = None
                elif silent_since is None: silent_since = now
                elif now - silent_since > 0.8: break
        if not recording: continue
        audio = np.concatenate(buf + [np.zeros(int(0.3 * SR), dtype="float32")])
        if len(audio) < SR * 0.4: print("   (too short, again)", flush=True); continue
        sf.write(out / f"{session}_{i+1:02d}.wav", audio, SR, subtype="PCM_16"); print("   ✓", flush=True); i += 1
print(f"\nDone: {session}. {len(items)} files in {out}")
