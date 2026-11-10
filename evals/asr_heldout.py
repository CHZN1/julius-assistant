"""Does the Whisper vocabulary hint generalise? Transcribes held-out recordings with and without the hint.

The held-out phrases (diagnostics/asr_heldout/phrases.json) avoid the hint vocabulary on purpose; two contain ordinary words that
double as team names (stars, wild). Reports mean WER both ways, and any hint word that appears in a transcript but is absent from
the reference (a hallucinated hint word). Record them with: python diagnostics/record_phrases.py heldout
Usage: python evals/asr_heldout.py [--dir diagnostics/asr_heldout]"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mlx.core as mx
import mlx_whisper
import numpy as np
import soundfile as sf
from audio_regression import SEED, wer

import julius.config as cfg
from julius.audio.speech_text import fix_transcript


def words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def hint_words(hint):
    return {
        w
        for term in hint.split(",")
        for w in words(term)
        if len(w) > 2 and w not in {"the", "red", "blue", "new", "los"}
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "diagnostics" / "asr_heldout"))
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
    if not clips:
        sys.exit(f"No recordings in {folder}; run: python diagnostics/record_phrases.py heldout")
    hint = cfg.asr_initial_prompt()
    vocab = hint_words(hint)
    repo = "mlx-community/whisper-small.en-mlx"
    mlx_whisper.transcribe(clips[0][1], path_or_hf_repo=repo, language="en")
    results = {}
    for name, prompt in (("no hint", None), ("hint", hint)):
        rows = []
        for n, audio in clips:
            mx.random.seed(SEED)
            r = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=repo,
                condition_on_previous_text=False,
                language="en",
                no_speech_threshold=0.6,
                **({"initial_prompt": prompt} if prompt else {}),
            )
            text = fix_transcript(r["text"].strip())
            ref = phrases[n - 1]
            ghost = sorted((set(words(text)) & vocab) - set(words(ref)))
            rows.append(
                {"n": n, "ref": ref, "heard": text, "wer": wer(ref, text), "hallucinated": ghost}
            )
        results[name] = rows
    print(f"{'':8} {'mean WER':>9} {'phrases with hallucinated hint words':>38}")
    for name, rows in results.items():
        print(
            f"{name:8} {100 * np.mean([r['wer'] for r in rows]):8.1f}% {sum(1 for r in rows if r['hallucinated']):>38}"
        )
    for a_, b_ in zip(results["no hint"], results["hint"], strict=False):
        flag = "  <-- hint hallucinated " + str(b_["hallucinated"]) if b_["hallucinated"] else ""
        if a_["heard"] != b_["heard"] or flag:
            print(
                f"#{a_['n']} ref:     {a_['ref']}\n    no hint: {a_['heard']}  (WER {a_['wer']:.2f})\n    hint:    {b_['heard']}  (WER {b_['wer']:.2f}){flag}"
            )
    better = np.mean([r["wer"] for r in results["hint"]]) <= np.mean(
        [r["wer"] for r in results["no hint"]]
    ) and not any(r["hallucinated"] for r in results["hint"])
    print(
        "\nVerdict:",
        "keep the hint (no worse on held-out phrases, nothing hallucinated)"
        if better
        else "the hint hurts held-out phrases; consider turning it off in julius.toml [asr] vocabulary_hint",
    )
    Path(folder / "results.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
