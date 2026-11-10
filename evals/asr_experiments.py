"""ASR variants on the recorded phrases: baseline vs input level normalisation vs vocabulary hint (Whisper initial_prompt).

Usage: python evals/asr_experiments.py [--dir diagnostics/asr_real] [--repeat 3]
Prints mean WER, phrases with missed key terms and per-phrase latency for each variant. Not a regression suite; use it to decide settings.
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mlx.core as mx
import mlx_whisper
import numpy as np
import soundfile as sf
from audio_regression import SEED, missed, wer

import julius.config as cfg
from julius.audio.speech_text import fix_transcript


def normalise(audio, peak=0.9):
    top = float(np.abs(audio).max())
    return audio if top < 1e-4 else (audio * (peak / top)).astype("float32")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "diagnostics" / "asr_real"))
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args()
    folder = Path(a.dir)
    phrases = json.loads((folder / "phrases.json").read_text())
    clips = [
        (
            int(p.stem.split("_")[1]),
            (lambda x: x[:, 0] if x.ndim > 1 else x)(sf.read(p, dtype="float32")[0]),
        )
        for p in sorted(folder.glob("phrase_*.wav"))
    ]
    hint = cfg.asr_initial_prompt()
    print(f"vocabulary hint: {len(hint.split())} words\n")
    variants = {
        "baseline": dict(norm=False, prompt=None),
        "normalised level": dict(norm=True, prompt=None),
        "vocabulary hint": dict(norm=False, prompt=hint),
        "hint + normalised": dict(norm=True, prompt=hint),
    }
    repo = "mlx-community/whisper-small.en-mlx"
    mlx_whisper.transcribe(clips[0][1], path_or_hf_repo=repo, language="en")  # load
    print(
        f"{'variant':20} {'mean WER':>9} {'phrases w/ missed terms':>24} {'missed terms':>13} {'mean s':>7} {'worst s':>8}"
    )
    for name, v in variants.items():
        wers, miss, times, heard = [], [], [], {}
        for n, audio in clips:
            x = normalise(audio) if v["norm"] else audio
            best = []
            for _ in range(a.repeat):
                mx.random.seed(SEED)
                t0 = time.perf_counter()
                r = mlx_whisper.transcribe(
                    x,
                    path_or_hf_repo=repo,
                    condition_on_previous_text=False,
                    language="en",
                    no_speech_threshold=0.6,
                    **({"initial_prompt": v["prompt"]} if v["prompt"] else {}),
                )
                best.append(time.perf_counter() - t0)
            text = fix_transcript(r["text"].strip())
            heard[n] = text
            wers.append(wer(phrases[n - 1], text))
            miss.append(missed(n, text))
            times.append(sorted(best)[len(best) // 2])
        print(
            f"{name:20} {100 * np.mean(wers):8.1f}% {sum(1 for m in miss if m):>24} {sum(len(m) for m in miss):>13} {np.mean(times):7.3f} {max(times):8.3f}"
        )
        for n, m in zip([c[0] for c in clips], miss):
            if m:
                print(f"      #{n} missed {m}: heard {heard[n][:90]!r}")


if __name__ == "__main__":
    main()
