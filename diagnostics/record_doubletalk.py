"""Record real double-talk for the barge-in eval: Julius's voice plays through your speakers while you interrupt him, the microphone and the
exact playback signal are saved on one clock. Saved to wake/data/doubletalk/ (git-ignored).

Three sessions of 12 takes each:
  stop      "Julius, stop." and similar commands          -> must interrupt
  question  a new "Julius, <question>" spoken over him    -> must interrupt
  julia     ordinary sentences with Julia/Julian/Julie    -> must NOT interrupt
Each take: the line to say is shown, Julius's long answer starts playing from a different point, and you say the line about 4 seconds in
(after he has been talking for a moment), as you would if you were interrupting a person. Speak at your normal volume from where you
normally sit, and keep your speaker volume where you usually have it.
Usage: python diagnostics/record_doubletalk.py stop|question|julia|all      (re-run to resume; existing takes are kept)"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
SR = 16000
ECHO = ROOT / "wake" / "data" / "echo"
OUT = ROOT / "wake" / "data" / "doubletalk"
REAL = ROOT / "diagnostics" / "wake_real"
TAKE_S = 9.0
STARTS = [0.0, 9.0, 18.0, 27.0]


def lines(kind):
    if kind == "stop":
        return [t for t, _ in json.load(open(REAL / "stop" / "prompts.json"))]
    if kind == "question":
        return [t for t, _ in json.load(open(REAL / "quick" / "prompts.json"))][:12]
    import re

    out = []
    for s in ("talk1", "talk2", "talk3", "talk4"):
        out += [
            t
            for t, _ in json.loads((REAL / s / "prompts.json").read_text())
            if re.search(r"\b(julia|julian|julie|jules)\b", t, re.I)
        ]
    return out[:12]


def main(kind):
    OUT.mkdir(parents=True, exist_ok=True)
    srcs = [sf.read(ECHO / f"long_{i}_src.wav", dtype="float32")[0] for i in range(3)]
    for k, text in enumerate(lines(kind), 1):
        name = f"{kind}_{k:02d}"
        if (OUT / f"{name}_mic.wav").exists():
            continue
        li = (k - 1) % 3
        start = STARTS[(k - 1) // 3 % len(STARTS)]
        seg = srcs[li][int(start * SR) : int((start + TAKE_S) * SR)]
        print(f"\n[{k}/12] SAY (about 4 s after Julius starts):   {text}", flush=True)
        print("   starting in 3 s ...", flush=True)
        time.sleep(3)
        time.sleep(0.5)
        x = np.concatenate(
            [np.zeros(int(0.5 * SR), np.float32), seg, np.zeros(int(0.8 * SR), np.float32)]
        ).reshape(-1, 1)
        rec = sd.playrec(x, samplerate=SR, channels=1, dtype="float32")
        sd.wait()
        sf.write(OUT / f"{name}_mic.wav", rec[:, 0], SR, subtype="PCM_16")
        sf.write(OUT / f"{name}_ref.wav", x[:, 0], SR, subtype="PCM_16")
        (OUT / f"{name}.json").write_text(
            json.dumps({"text": text, "kind": kind, "long": li, "start_s": start})
        )
        print("   ✓", flush=True)
    print("done:", kind)


if __name__ == "__main__":
    for kind in (
        ["stop", "question", "julia"] if (sys.argv[1:] or ["all"])[0] == "all" else sys.argv[1:2]
    ):
        main(kind)
