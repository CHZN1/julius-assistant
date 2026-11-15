"""Labelled synthetic music for tests and the accuracy eval: a drum pattern at a known tempo under chords in a known key. Deterministic, licence-free, never stored."""

from __future__ import annotations

import numpy as np

SR = 22050
_NAMES = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "Eb": 3,
    "D#": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "Bb": 10,
    "A#": 10,
    "B": 11,
}
# chord roots (semitones above the tonic) and triad shapes, one chord per bar
_MAJOR = [(0, (0, 4, 7)), (5, (0, 4, 7)), (7, (0, 4, 7)), (0, (0, 4, 7))]
_MINOR = [(0, (0, 3, 7)), (8, (0, 4, 7)), (10, (0, 4, 7)), (0, (0, 3, 7))]


def _tone(freq: float, n: int, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(n) / SR
    out = sum((0.6**h) * np.sin(2 * np.pi * freq * (h + 1) * t) for h in range(5))
    env = np.minimum(1.0, t * 30) * np.minimum(1.0, (n / SR - t) * 30 + 0.0)
    return out * env


def render(
    bpm: float,
    key: str,
    seconds: float = 30.0,
    seed: int = 0,
    drums: bool = True,
    tonal: bool = True,
) -> np.ndarray:
    """Mono float32 audio. `key` like 'A minor' or 'F# major'."""
    tonic_name, mode = key.split()
    tonic = _NAMES[tonic_name]
    rng = np.random.default_rng(seed)
    total = int(seconds * SR)
    y = np.zeros(total)
    beat = 60.0 / bpm
    bar = beat * 4
    prog = _MINOR if mode == "minor" else _MAJOR
    k = 0
    while tonal and k * bar < seconds:
        root, shape = prog[k % len(prog)]
        start = int(k * bar * SR)
        n = min(int(bar * SR), total - start)
        if n <= 0:
            break
        for s in shape:
            midi = 48 + tonic + root + s  # around C3
            y[start : start + n] += 0.12 * _tone(440.0 * 2 ** ((midi - 69) / 12), n, rng)
        y[start : start + n] += 0.12 * _tone(
            440.0 * 2 ** ((36 + tonic + root - 69) / 12), n, rng
        )  # bass
        k += 1
    if drums:
        t_k = np.arange(int(0.18 * SR)) / SR
        kick = np.sin(2 * np.pi * (50 * t_k + 70 * (1 - np.exp(-t_k * 30)) / 30)) * np.exp(
            -t_k * 22
        )
        snare = rng.standard_normal(int(0.12 * SR)) * np.exp(-np.arange(int(0.12 * SR)) / SR * 35)
        hat = rng.standard_normal(int(0.03 * SR)) * np.exp(-np.arange(int(0.03 * SR)) / SR * 150)
        b = 0
        while b * beat / 2 < seconds:
            at = int(b * beat / 2 * SR)
            if b % 2 == 0:
                if (b // 2) % 2 == 1:
                    y[at : at + len(snare)] += (
                        0.5 * snare[: max(0, total - at)][: len(snare)] if at < total else 0
                    )
                y[at : at + len(kick)][: max(0, total - at)] += (
                    0.9 * kick[: max(0, total - at)] if (b // 2) % 2 == 0 and at < total else 0
                )
            y[at : at + len(hat)][: max(0, total - at)] += (
                0.18 * hat[: max(0, total - at)] if at < total else 0
            )
            b += 1
    y += 0.002 * rng.standard_normal(total)
    return (y / (np.max(np.abs(y)) + 1e-9) * 0.8).astype(np.float32)


def write(
    path, bpm: float, key: str, seconds: float = 30.0, seed: int = 0, drums: bool = True
) -> None:
    import soundfile as sf

    sf.write(str(path), render(bpm, key, seconds, seed, drums), SR, subtype="PCM_16")
