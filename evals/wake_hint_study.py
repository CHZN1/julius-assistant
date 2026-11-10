"""Which Whisper hint serves BOTH the wake check and the request? Train-side data only (wake, talk1+2, asr_real, asr_heldout); wake2/wake3/talk3 are not used.

Hints: vocab (today), none, contrast ("Julius. Hey Julius. Julia. Julian ..."), combined (contrast terms + the vocabulary list without a bare "Julius").
Reports: wake-word misses on the 30 full recordings, false accepts on Julia/Julian/jealous sentences, mean WER on the 20 + 10 phrase sets."""

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
from julius.audio.listening import has_wake
from julius.audio.speech_text import fix_transcript

CONTRAST = "Julius. Hey Julius. Julia. Julian. Julie. Jules."


def combined():
    return cfg.asr_initial_prompt(wake=True)


HINTS = {
    "none": None,
    "vocab": cfg.asr_initial_prompt(),
    "contrast": CONTRAST,
    "combined": combined(),
}
repo = "mlx-community/whisper-small.en-mlx"


def tr(a, h):
    mx.random.seed(SEED)
    return fix_transcript(
        mlx_whisper.transcribe(
            a,
            path_or_hf_repo=repo,
            condition_on_previous_text=False,
            language="en",
            no_speech_threshold=0.6,
            **({"initial_prompt": h} if h else {}),
        )["text"].strip()
    )


def load(folder, pattern):
    return [
        (p.name, (lambda x: x[:, 0] if x.ndim > 1 else x)(sf.read(p, dtype="float32")[0]))
        for p in sorted(Path(folder).glob(pattern))
    ]


wake = load(ROOT / "diagnostics/wake_real/wake", "wake_*.wav")
names = []
for s in ("talk1", "talk2"):
    pr = json.loads((ROOT / f"diagnostics/wake_real/{s}/prompts.json").read_text())
    cl = load(ROOT / f"diagnostics/wake_real/{s}", f"{s}_*.wav")
    names += [
        (pr[i][0], a)
        for i, (_, a) in enumerate(cl)
        if re.search(r"julia|julian|jealous|julie|jules", pr[i][0].lower())
    ]
sets = {"asr20": (ROOT / "diagnostics/asr_real",), "heldout10": (ROOT / "diagnostics/asr_heldout",)}
tr(wake[0][1], None)
print(f"{len(wake)} wake recordings, {len(names)} sound-alike sentences\n")
for hn, h in HINTS.items():
    miss = sum(not has_wake(tr(a, h)) for _, a in wake)
    fa = sum(has_wake(tr(a, h)) for _, a in names)
    out = [f"{hn:9} wake miss {miss}/30, false accepts {fa}/{len(names)}"]
    for sn, (folder,) in sets.items():
        ph = json.loads((folder / "phrases.json").read_text())
        cl = load(folder, "phrase_*.wav")
        out.append(
            f"WER {sn} {100 * np.mean([wer(ph[int(n[7:9]) - 1], tr(a, h)) for n, a in cl]):.1f}%"
        )
    print(" | ".join(out), flush=True)
