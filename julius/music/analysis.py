"""BPM and key detection for an audio file, entirely on this machine and read-only (librosa, ISC licence).

Tempo: onset-strength autocorrelation with a mild preference for 70-180 BPM, refined from the beat positions; the half/double-time alternative is reported when it is
nearly as strong. Key: Krumhansl-Kessler key profiles correlated with the file's average chroma. Both come with a 0-1 confidence that only means "how clearly this
beat the runner-up", not a probability; accuracy on labelled material is measured by evals/audio_eval.py and tests/test_music_analysis.py."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from julius.music import policy

SR = 22050
HOP = 256
MAX_SECONDS = 90.0  # analysed window; longer files are read from the middle
MAX_BYTES = 400 * 1024 * 1024
BPM_RANGE = (50.0, 200.0)
FOLD = (80.0, 160.0)

NAMES_MAJOR = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
NAMES_MINOR = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "G#", "A", "Bb", "B"]
# Krumhansl & Kessler (1982) probe-tone profiles, tonic first.
KK_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KK_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


class AudioProblem(Exception):
    """A file that cannot or must not be analysed; the message is what Julius says."""


@dataclass
class Analysis:
    path: str
    name: str
    duration: float
    bpm: float
    bpm_confidence: float
    bpm_alternatives: list[float] = field(default_factory=list)
    key: str = ""
    key_confidence: float = 0.0
    key_runner_up: str = ""
    analysed_seconds: float = 0.0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def clock(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def check_path(path: str | Path) -> Path:
    """The file to analyse: it must exist, be a plain audio file under the size cap and outside every protected music location."""
    p = Path(os.path.expanduser(str(path)))
    why = policy.denied(p)
    if why:
        raise AudioProblem(f"I won't look at {p.name}: {why}.")
    if not p.is_file():
        raise AudioProblem(f"I couldn't find {p.name}.")
    if p.suffix.casefold() not in policy.AUDIO_EXT:
        raise AudioProblem(
            f"{p.name} isn't an audio file type I read (wav, aiff, mp3, flac, m4a, caf, ogg)."
        )
    if p.stat().st_size > MAX_BYTES:
        raise AudioProblem(f"{p.name} is too large to analyse (over 400 MB).")
    return p


def _decode_with_afconvert(p: Path, out: Path) -> None:
    exe = shutil.which("afconvert") or "/usr/bin/afconvert"
    r = subprocess.run(
        [exe, "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1", str(p), str(out)],
        capture_output=True,
        timeout=120,
    )
    if r.returncode != 0 or not out.exists():
        raise AudioProblem(f"I couldn't read the audio in {p.name}.")


def load(p: Path) -> tuple[Any, float, float]:
    """(mono samples at 22.05 kHz, full duration in seconds, seconds analysed). Never writes beside the original; any decoding copy lives in a temp folder."""
    import librosa
    import soundfile as sf

    def read(src: Path) -> tuple[Any, float, float]:
        info = sf.info(str(src))
        total = float(info.frames) / float(info.samplerate)
        offset = max(0.0, total / 2 - MAX_SECONDS / 2) if total > MAX_SECONDS else 0.0
        y, _ = librosa.load(str(src), sr=SR, mono=True, offset=offset, duration=MAX_SECONDS)
        return y, total, float(len(y)) / SR

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return read(p)
        except Exception:
            pass
        with tempfile.TemporaryDirectory(prefix="julius-audio-") as tmp:
            wav = Path(tmp) / "decoded.wav"
            _decode_with_afconvert(p, wav)
            try:
                return read(wav)
            except Exception as exc:
                raise AudioProblem(f"I couldn't read the audio in {p.name}.") from exc


# ---- tempo ---------------------------------------------------------------------------------------------------------------------------------------------------
def _parabolic(values: Any, i: int) -> float:
    """Sub-sample peak position around index i."""
    if i <= 0 or i >= len(values) - 1:
        return float(i)
    a, b, c = values[i - 1], values[i], values[i + 1]
    d = a - 2 * b + c
    return float(i) if d == 0 else float(i) + 0.5 * (a - c) / d


def tempo(y: Any) -> tuple[float, float, list[float]]:
    """(bpm, confidence 0-1, alternative readings such as half or double time)."""
    import librosa
    import numpy as np

    env_raw = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    env = env_raw
    if len(env) < 64 or float(np.max(env)) <= 1e-6:
        raise AudioProblem("There isn't a clear beat in that audio.")
    env = env - env.mean()
    n = len(env)
    f = np.fft.rfft(env, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac = ac / (ac[0] + 1e-12)
    fps = SR / HOP
    lo, hi = int(fps * 60 / BPM_RANGE[1]), int(fps * 60 / BPM_RANGE[0])
    hi = min(hi, n - 2)
    lags = np.arange(lo, hi + 1)
    bpms = 60.0 * fps / lags
    # a gentle preference for 70-180 BPM, flat inside it, so slow or fast material is not forced to a middle value
    prior = np.exp(-0.5 * (np.log2(np.clip(bpms, 1, None) / 120.0) / 1.3) ** 2)
    score = ac[lags] * prior
    i = int(np.argmax(score))
    lag = float(_parabolic(ac, int(lags[i])))
    bpm = float(60.0 * fps / lag)
    peak = float(ac[lags[i]])

    def at(b: float) -> float:
        k = int(round(fps * 60.0 / b))
        if k < 1 or k + 1 >= n:
            return 0.0
        return float(np.max(ac[max(1, k - 1) : k + 2]))

    # runner-up: the strongest lag that is not this tempo or a simple multiple/fraction of it
    related = [bpm * r for r in (0.5, 2 / 3, 0.75, 1.0, 4 / 3, 1.5, 2.0)]
    other = 0.0
    for j, b in enumerate(bpms):
        if any(abs(b - r) / r < 0.06 for r in related):
            continue
        other = max(other, float(score[j]))
    top = float(score[i])
    confidence = max(0.0, min(1.0, (top - other) / (top + 1e-9) * 2.0)) if top > 0 else 0.0
    confidence *= max(0.0, min(1.0, peak / 0.25))
    alternatives = []
    for r in (0.5, 2.0):
        b = bpm * r
        if BPM_RANGE[0] <= b <= BPM_RANGE[1] and at(b) >= 0.6 * peak:
            alternatives.append(float(b))
    # most music sits between 80 and 160 BPM: when the half or double reading is nearly as strong and lands there, prefer it
    if not FOLD[0] <= bpm < FOLD[1]:
        for b in list(alternatives):
            if FOLD[0] <= b < FOLD[1]:
                alternatives = [x for x in alternatives if x != b] + [bpm]
                bpm = b
                break
    bpm = _refine(env_raw=env_raw, bpm=bpm)
    return bpm, round(confidence, 2), [round(x, 1) for x in alternatives]


def _refine(env_raw: Any, bpm: float) -> float:
    """Sharpen the estimate from beat positions when they agree with it (steady material); otherwise keep it."""
    import librosa
    import numpy as np

    try:
        _, beats = librosa.beat.beat_track(
            onset_envelope=env_raw, sr=SR, hop_length=HOP, start_bpm=bpm, tightness=300, trim=False
        )
    except Exception:
        return bpm
    if len(beats) < 8:
        return bpm
    t = librosa.frames_to_time(beats, sr=SR, hop_length=HOP)
    slope = float(np.polyfit(np.arange(len(t)), t, 1)[0])
    if slope <= 0:
        return bpm
    fine = 60.0 / slope
    return fine if abs(fine - bpm) / bpm < 0.03 else bpm


# ---- key -----------------------------------------------------------------------------------------------------------------------------------------------------
def key_of(y: Any) -> tuple[str, float, str]:
    """(key like 'A minor', confidence 0-1, runner-up key)."""
    import librosa
    import numpy as np

    harmonic = librosa.effects.harmonic(y, margin=3.0)
    tuning = float(librosa.estimate_tuning(y=harmonic, sr=SR))
    chroma = librosa.feature.chroma_cqt(
        y=harmonic, sr=SR, hop_length=512, tuning=tuning, n_octaves=6, fmin=librosa.note_to_hz("C2")
    )
    weight = chroma.sum(axis=0)
    if float(weight.sum()) <= 1e-6:
        raise AudioProblem("There isn't enough pitched sound in that audio to name a key.")
    profile = (chroma * weight).sum(axis=1) / weight.sum()
    scores: list[tuple[float, str]] = []
    for table, names, mode in ((KK_MAJOR, NAMES_MAJOR, "major"), (KK_MINOR, NAMES_MINOR, "minor")):
        for tonic in range(12):
            ref = np.roll(table, tonic)
            r = float(np.corrcoef(profile, ref)[0, 1])
            scores.append((r, f"{names[tonic]} {mode}"))
    scores.sort(reverse=True)
    (r1, k1), (r2, k2) = scores[0], scores[1]
    confidence = max(0.0, min(1.0, (r1 - r2) / 0.12)) * max(0.0, min(1.0, (r1 - 0.2) / 0.5))
    return k1, round(confidence, 2), k2


# ---- the whole thing -----------------------------------------------------------------------------------------------------------------------------------------
def analyze(path: str | Path) -> Analysis:
    p = check_path(path)
    y, total, seconds = load(p)
    if seconds < 3.0:
        raise AudioProblem(f"{p.name} is too short to find a tempo or key (under 3 seconds).")
    bpm, bpm_conf, alts = tempo(y)
    note = ""
    try:
        key, key_conf, runner = key_of(y)
    except AudioProblem as exc:
        key, key_conf, runner, note = "", 0.0, "", str(exc)
    if total > seconds + 1:
        note = (note + " " if note else "") + f"Analysed {int(seconds)} seconds from the middle."
    return Analysis(
        path=str(p),
        name=p.name,
        duration=round(total, 2),
        bpm=round(bpm, 1),
        bpm_confidence=bpm_conf,
        bpm_alternatives=alts,
        key=key,
        key_confidence=key_conf,
        key_runner_up=runner,
        analysed_seconds=round(seconds, 1),
        note=note,
    )


def level(conf: float) -> str:
    return "high" if conf >= 0.66 else "medium" if conf >= 0.33 else "low"


def table_rows(a: Analysis) -> list[list[str]]:
    """The rows of the panel card: what was found and how sure it is."""
    bpm = f"{a.bpm:g}" + (
        f" (or {' / '.join(f'{b:g}' for b in a.bpm_alternatives)})" if a.bpm_alternatives else ""
    )
    rows = [
        ["Tempo", f"{bpm} BPM", f"{level(a.bpm_confidence)} ({a.bpm_confidence:.2f})"],
        [
            "Key",
            a.key or "not found",
            f"{level(a.key_confidence)} ({a.key_confidence:.2f})" if a.key else "",
        ],
        ["Duration", clock(a.duration), ""],
    ]
    if a.key and a.key_runner_up and a.key_confidence < 0.66:
        rows.append(["Also possible", a.key_runner_up, ""])
    return rows


def spoken(a: Analysis) -> str:
    """What the model is told, in plain facts."""
    alt = (
        f" It could also be felt at {' or '.join(f'{b:g}' for b in a.bpm_alternatives)}."
        if a.bpm_alternatives
        else ""
    )
    key = (
        f" Key: {a.key} ({level(a.key_confidence)} confidence)."
        if a.key
        else " I couldn't find a key."
    )
    if a.key and a.key_confidence < 0.66 and a.key_runner_up:
        key += f" It might also be {a.key_runner_up}."
    return (
        f"{a.name}: about {a.bpm:g} BPM ({level(a.bpm_confidence)} confidence).{alt}{key} Length {clock(a.duration)}."
        + (f" {a.note}" if a.note else "")
    )
