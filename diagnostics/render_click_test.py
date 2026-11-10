"""Render real Julius replies with the MLX voice (no playback) and look for clicks.

Per reply it records every streamed chunk (arrival time, length), joins them exactly as the player receives them, saves the result to
diagnostics/click_test/, and reports (1) discontinuities at chunk seams against ordinary sample-to-sample movement, (2) the
first/last sample of each sentence (a non-zero edge pops when a stream starts or stops), and (3) a playback simulation: whether each chunk
arrived before the previous audio ran out (an underrun is an audible gap and pop).
Usage: python diagnostics/render_click_test.py [prefill_seconds ...]   (default 0 0.25 0.5)
"""

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["JULIUS_TTS"] = "qwen-mlx"
os.environ.setdefault("JULIUS_MLX_KEEP_WORKER", "1")
sys.path.insert(0, str(ROOT))
import numpy as np
import soundfile as sf
from julius_speech_text import normalize_for_speech
from julius_tts import SpeechEngine
from julius_voice_identity import delivery_mode

REPLIES = {
    "overfitting": "Overfitting is when a model learns the training data too closely, including its random noise, so it does well on that data but poorly on new data it hasn't seen. The core idea is that it memorizes instead of learning the general pattern. Want more detail?",
    "springfield": "It's 76 degrees and clear in Springfield, feels like 75, with a high of 77 and a low of 50.",
    "eth": "Ethereum's at about 2,309 dollars, down around 0.8 percent today. That quote's delayed, so treat it as a snapshot.",
}
OUT = ROOT / "diagnostics" / "click_test"
OUT.mkdir(exist_ok=True)
prefills = [float(x) for x in sys.argv[1:]] or [0.0, 0.25, 0.5]


def seam_score(x, b, win=80):
    """Jump at boundary b divided by the 99th-percentile sample-to-sample movement in the 5 ms around it."""
    jump = abs(float(x[b]) - float(x[b - 1]))
    lo, hi = max(1, b - win), min(len(x) - 1, b + win)
    d = np.abs(np.diff(x[lo:hi]))
    d = np.delete(d, min(len(d) - 1, b - lo - 1)) if len(d) > 1 else d
    return jump / max(float(np.percentile(d, 99)), 1e-4), jump


def simulate(arrivals, durations, prefill):
    """Playback starts once `prefill` seconds of audio are buffered (or the sentence ends). Returns (underruns, total gap seconds)."""
    buffered, i, n = 0.0, 0, len(arrivals)
    while i < n and buffered < prefill:
        buffered += durations[i]
        i += 1
    start = arrivals[max(0, i - 1)]
    played_until, gaps, gap_total = start + sum(durations[:i]), 0, 0.0
    for k in range(i, n):
        if arrivals[k] > played_until:
            gaps += 1
            gap_total += arrivals[k] - played_until
            played_until = arrivals[k]
        played_until += durations[k]
    return gaps, gap_total


engine = SpeechEngine()
report = {}
for name, reply in REPLIES.items():
    sentences = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", reply) if len(s.strip()) > 2]
    pieces, seams, edges, sim = [], [], [], {p: [0, 0.0] for p in prefills}
    rate = None
    for sentence in sentences:
        text = normalize_for_speech(sentence)
        engine.response_delivery_mode = delivery_mode(text)
        speed = 1.05 * (
            0.97
            if text.endswith("?")
            else 1.06
            if text.endswith("!")
            else 1.04
            if len(text) < 25
            else 1.0
        )
        t0 = time.perf_counter()
        chunks, arrivals = [], []
        for audio, sr in engine.synthesize(text, speed=speed):
            arrivals.append(time.perf_counter() - t0)
            chunks.append(np.asarray(audio, dtype=np.float32).reshape(-1))
            rate = sr
        durations = [len(c) / rate for c in chunks]
        for p in prefills:
            g, total = simulate(arrivals, durations, p)
            sim[p][0] += g
            sim[p][1] += total
        wave = np.concatenate(chunks)
        cuts = np.cumsum([len(c) for c in chunks])[:-1]
        for b in cuts:
            seams.append(seam_score(wave, int(b)))
        edges.append((float(abs(wave[0])), float(abs(wave[-1]))))
        report.setdefault(name, {}).setdefault("chunks", []).append(
            dict(
                sentence=text[:50],
                chunks=len(chunks),
                audio_s=round(sum(durations), 2),
                generate_s=round(arrivals[-1], 2),
                first_chunk_s=round(arrivals[0], 2),
                chunk_seconds=[round(d, 2) for d in durations],
                arrivals=[round(a, 2) for a in arrivals],
            )
        )
        pieces += [wave, np.zeros(int(0.3 * rate), dtype=np.float32)]
    sf.write(OUT / f"{name}_as_streamed.wav", np.concatenate(pieces), rate)
    ratios = np.array([r for r, _ in seams]) if seams else np.array([0.0])
    # reference: the same score at arbitrary interior points of the audio (what an ordinary sample looks like)
    whole = np.concatenate(pieces)
    rnd = [
        seam_score(whole, int(b))[0]
        for b in np.random.default_rng(3).integers(500, len(whole) - 500, 400)
    ]
    report[name].update(
        seams=len(seams),
        seam_ratio_median=round(float(np.median(ratios)), 2),
        seam_ratio_max=round(float(ratios.max()), 2),
        interior_ratio_p99=round(float(np.percentile(rnd, 99)), 2),
        seams_above_interior_p99=int((ratios > np.percentile(rnd, 99)).sum()),
        worst_seam_jump=round(max((j for _, j in seams), default=0.0), 4),
        sentence_edge_first_sample_max=round(max(e[0] for e in edges), 4),
        sentence_edge_last_sample_max=round(max(e[1] for e in edges), 4),
        simulated_underruns={
            str(p): dict(underruns=v[0], gap_seconds=round(v[1], 2)) for p, v in sim.items()
        },
    )
    print(name, json.dumps({k: v for k, v in report[name].items() if k != "chunks"}), flush=True)
    for c in report[name]["chunks"]:
        print(
            "   ",
            c["sentence"],
            f"| {c['chunks']} chunks, audio {c['audio_s']}s, generated in {c['generate_s']}s (first chunk {c['first_chunk_s']}s)",
        )
engine.close()
(OUT / "report.json").write_text(json.dumps(report, indent=1))
