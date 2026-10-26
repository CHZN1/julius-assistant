"""Longest pause inside each saved utterance (JULIUS_SAVE_AUDIO_DIR), and which silence-wait settings would have cut it.

Usage: python diagnostics/analyze_pauses.py diagnostics/live_test/audio
"""
import sys
from pathlib import Path
import numpy as np, soundfile as sf
SR, CHUNK, TH = 16000, 1024, 0.0045
SETTINGS = [1.2, 0.8, 0.6]
folder = Path(sys.argv[1]); rows = []
for path in sorted(folder.glob("*.wav")):
    audio, rate = sf.read(path, dtype="float32"); assert rate == SR
    audio = audio[:, 0] if audio.ndim > 1 else audio
    voiced = [float(np.abs(audio[i:i + CHUNK]).mean()) > TH for i in range(0, len(audio) - CHUNK, CHUNK)]
    idx = [i for i, v in enumerate(voiced) if v]
    gap = longest = 0
    for a, b in zip(idx, idx[1:]):  # silence between the first and last voiced chunk only
        longest = max(longest, (b - a - 1) * CHUNK / SR)
    rows.append((path.name, len(audio) / SR, longest))
    print(f"{path.name}: {len(audio)/SR:4.1f}s, longest pause inside the sentence {longest:.2f}s; " + ", ".join(f"{s:.1f}s wait {'CUTS' if longest > s else 'ok'}" for s in SETTINGS))
if rows:
    worst = max(r[2] for r in rows)
    print(f"\nlongest internal pause across {len(rows)} utterances: {worst:.2f}s -> shortest setting that never cuts: " + (", ".join(f"{s:.1f}s" for s in SETTINGS if worst <= s)[-4:] if any(worst <= s for s in SETTINGS) else "none of the tested settings"))
