"""Silence-wait benchmark: replay Julius's real endpoint logic on speech with a controlled mid-sentence pause.

Clips are macOS `say` speech (two clauses) joined by a pause of N seconds of room noise, with a long quiet tail. For each
silence setting (JULIUS_SILENCE_SECONDS) the simulator reports whether the sentence was cut in two. Usage: python bench_endpoint.py
"""

import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

SR, CHUNK, TH, MIN_SPEECH = 16000, 1024, 0.0045, 0.4
PAIRS = [
    ("What's the weather like", "in Springfield right now?"),
    ("Explain overfitting", "in simple terms please."),
    ("Julius, what's the price of", "Ethereum right now?"),
    ("Plan a three day trip to Tokyo", "and include the weather."),
    ("I've been training a network", "and the validation accuracy keeps plateauing."),
]
GAPS = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.3]
SETTINGS = [1.2, 0.8, 0.6]
rng = np.random.default_rng(1)
work = Path(tempfile.mkdtemp())


def say(text, i):
    out = work / f"c{i}.wav"
    subprocess.run(
        [
            "say",
            "-v",
            "Samantha",
            "-o",
            str(out),
            "--file-format=WAVE",
            "--data-format=LEI16@16000",
            text,
        ],
        check=True,
    )
    audio, rate = sf.read(out, dtype="float32")
    assert rate == SR
    return audio[:, 0] if audio.ndim > 1 else audio


def noise(seconds):
    return (rng.normal(0, 0.0007, int(seconds * SR))).astype(
        "float32"
    )  # well under the 0.0045 trigger


def endpoints(audio, silence):
    """Mirror of the capture loop in julius_candidate.py: returns the number of utterances it would cut the clip into."""
    recording, sil_t, spk_t, voiced_run, longest, cuts = False, None, None, 0, 0, 0
    for i in range(0, len(audio) - CHUNK, CHUNK):
        now = i / SR
        lvl = float(np.abs(audio[i : i + CHUNK]).mean())
        if lvl > TH:
            if not recording:
                recording, spk_t, voiced_run, longest = True, now, 0, 0
            voiced_run += 1
            longest = max(longest, voiced_run)
            sil_t = None
        elif recording:
            voiced_run = 0
            if sil_t is None:
                sil_t = now
            elif now - sil_t > silence:
                if now - spk_t >= MIN_SPEECH and longest >= 2:
                    cuts += 1
                recording, sil_t = False, None
    return cuts


clips = [(say(a, 2 * k), say(b, 2 * k + 1)) for k, (a, b) in enumerate(PAIRS)]
print(
    f"{len(PAIRS)} two-clause sentences x {len(GAPS)} pause lengths; endpoint logic identical to the runtime (threshold {TH}, 64 ms chunks)\n"
)
print("mid-sentence pause |" + "|".join(f" wait {s:.1f}s " for s in SETTINGS))
result = {s: [] for s in SETTINGS}
for gap in GAPS:
    row = []
    for s in SETTINGS:
        cut = 0
        for first, second in clips:
            audio = np.concatenate([noise(1.0), first, noise(gap), second, noise(2.5)])
            cut += endpoints(audio, s) > 1  # more than one utterance = the sentence was split
        row.append(cut)
        result[s].append(cut)
    print(f"      {gap:>4.1f} s      |" + "|".join(f"  {c}/{len(clips)} cut " for c in row))
print()
for s in SETTINGS:
    safe = [g for g, c in zip(GAPS, result[s], strict=False) if c == 0]
    print(
        f"wait {s:.1f} s: keeps sentences whole up to a {max(safe):.1f} s pause; adds {s:.1f} s after you stop; saves {1.2 - s:.1f} s vs the current 1.2 s"
    )
