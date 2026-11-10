"""Record what Julius's own speech sounds like at your microphone (the echo), for the barge-in eval. Stay SILENT while it runs (about 4 minutes).

Plays Julius's real voice (the 18 held-out lines and three long answers, rendered once with his Qwen voice) through the default output at the
volume it is set to now, and records the microphone on the same clock. Files go to wake/data/echo/ (git-ignored): <name>_ref.wav is what was
played, <name>_mic.wav is what the microphone heard. Use the speaker volume you normally listen at, and sit where you normally sit.
Usage: python diagnostics/record_echo.py"""

import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "wake"))
SR = 16000
OUT = ROOT / "wake" / "data" / "echo"
OUT.mkdir(parents=True, exist_ok=True)
LONG = [
    "Sure. Here is how I would approach it. First, look at the data you already have and decide what question you are really asking, because the best model in the world cannot answer a question that the data does not contain. Second, build the simplest baseline you can, a plain average or a linear model, and measure it honestly on games the model has never seen. Third, only then add features one at a time, keeping each one only if it improves the held out score by more than the noise. Finally, write down what you tried, including the things that failed, so that in a month you remember why the final version looks the way it does.",
    "Let me walk you through the trip. On the first day you land in the afternoon, so keep it light: drop your bags, walk around the neighborhood near the hotel, and find an early dinner. On the second day start with the morning market while it is cool, then take the train out to the old town for lunch and spend the afternoon at the castle gardens. On the third day leave the mornings open for anything you missed, pack in the afternoon, and give yourself plenty of time to get back to the airport, because the trains can be slow on the weekend.",
    "Here is the weather picture for the week. Today is clear and warm, with a high near ninety and a light breeze from the west. Tomorrow looks similar, a few degrees cooler in the evening. On Thursday a weak system moves through and brings a chance of afternoon showers, mostly over the foothills. Friday clears out again, and the weekend looks dry and pleasant, with highs in the low eighties. If you are planning anything outdoors, Thursday morning and the whole weekend are your best bets.",
]


def render():
    import build_dataset as bd

    p = OUT / "long_0_src.wav"
    if p.exists():
        return
    print("rendering Julius's voice for the long answers (one time) ...", flush=True)
    for i, (_text, x) in enumerate(bd.julius_voice_clips(LONG)):
        sf.write(
            OUT / f"long_{i}_src.wav", (x / (np.abs(x).max() + 1e-6) * 0.7).astype(np.float32), SR
        )


def clips():
    out = [(f"long_{i}", sf.read(OUT / f"long_{i}_src.wav", dtype="float32")[0]) for i in range(3)]
    out += [
        (f"line_{p.stem}", sf.read(p, dtype="float32")[0])
        for p in sorted((ROOT / "wake" / "data" / "eval_julius").glob("*.wav"))
    ]
    return out


def main():
    render()
    todo = clips()
    print(f"{len(todo)} clips, stay silent. Starting in 3 s ...", flush=True)
    time.sleep(3)
    for name, ref in todo:
        if (OUT / f"{name}_mic.wav").exists():
            continue
        x = np.concatenate(
            [np.zeros(int(0.5 * SR), np.float32), ref, np.zeros(int(1.0 * SR), np.float32)]
        ).reshape(-1, 1)
        rec = sd.playrec(x, samplerate=SR, channels=1, dtype="float32")
        sd.wait()
        sf.write(OUT / f"{name}_mic.wav", rec[:, 0], SR, subtype="PCM_16")
        sf.write(OUT / f"{name}_ref.wav", x[:, 0], SR, subtype="PCM_16")
        print("  ✓", name, f"{len(ref) / SR:.1f}s", flush=True)
        time.sleep(0.8)
    print("done:", OUT)


if __name__ == "__main__":
    main()
