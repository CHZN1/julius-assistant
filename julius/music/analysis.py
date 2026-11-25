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
# Below this share of harmonic (pitched) energy there is no key to name: drum-only loops measured at most 0.011, the quietest melodic loops at least 0.017 (evals/music_filename_eval.py keys)
MIN_HARMONIC_SHARE = 0.015

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
    key_clear: bool = True
    filename_bpm: int | None = None
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


def _decode_audio(p: Path, out: Path) -> None:
    if exe := shutil.which("ffmpeg"):
        cmd = [
            exe,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-n",
            "-i",
            str(p),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SR),
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
            str(out),
        ]
    elif exe := shutil.which("afconvert"):
        cmd = [exe, "-f", "WAVE", "-d", f"LEI16@{SR}", "-c", "1", str(p), str(out)]
    else:
        raise AudioProblem("I have no audio converter: install ffmpeg, or use macOS afconvert.")
    r = subprocess.run(
        cmd,
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
            _decode_audio(p, wav)
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


PRIOR_CENTER = 130.0  # chosen on the dev half of the filename eval (evals/music_filename_eval.py), checked on the held-out half
PRIOR_WIDTH = 0.7  # octaves
HARMONICS = (1, 2, 3, 4)
SWITCH = 1.1  # a 3:2 or 4:3 faster reading wins when its beat grid hits onsets this much better than the first choice


def _acf(env: Any) -> Any:
    import numpy as np

    e = env - env.mean()
    n = len(e)
    f = np.fft.rfft(e, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    return ac / (ac[0] + 1e-12)


def _pulse(env: Any, period: float) -> float:
    """Beat strength: the mean onset strength on a grid of this period (frames), at the best phase, relative to the file's mean. A real beat grid lands on the hits; a wrong 3:2 or 4:3 grid half-lands on weak ones."""
    import numpy as np

    k = int((len(env) - period - 1) // period)
    if k < 4:
        return 0.0
    idx = np.rint(np.arange(0, period, 1.0)[:, None] + period * np.arange(k)[None, :]).astype(int)
    return float(env[idx].mean(axis=1).max() / (env.mean() + 1e-9))


def _beat_strength(env: Any, bpm: float, fps: float) -> float:
    """_pulse at the best tempo within 2% of bpm (a grid has to be exact to line up over a whole file)."""
    import numpy as np

    return max(_pulse(env, fps * 60.0 / (bpm * (1 + d))) for d in np.arange(-0.02, 0.0201, 0.0006))


def tempo(y: Any) -> tuple[float, float, list[float]]:
    """(bpm, confidence 0-1, alternative readings such as half or double time).

    Each tempo from 55 to 200 BPM is scored by how well the onset-strength autocorrelation repeats at one, two, three and four of its beats (a comb, so a pattern
    that repeats every 1.5 beats does not beat the real beat), times a soft preference for the middle of the range. The first choice is then checked against its
    3:2 and 4:3 faster readings, which are the usual mistake on triplet-feel material; the faster one wins only when its beat grid lands clearly better."""
    import librosa
    import numpy as np

    env_raw = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    if len(env_raw) < 64 or float(np.max(env_raw)) <= 1e-6:
        raise AudioProblem("There isn't a clear beat in that audio.")
    n = len(env_raw)
    fps = SR / HOP
    ac = _acf(env_raw)
    grid = np.arange(55.0, 200.01, 1.0)
    comb = np.zeros(len(grid))
    for j, b in enumerate(grid):
        lag = fps * 60.0 / b
        v = [ac[int(round(lag * m))] for m in HARMONICS if int(round(lag * m)) < n - 1]
        comb[j] = max(float(np.mean(v)), 0.0) if v else 0.0
    prior = np.exp(-0.5 * (np.log2(grid / PRIOR_CENTER) / PRIOR_WIDTH) ** 2)
    score = comb * prior
    i = int(np.argmax(score))
    bpm = float(grid[i])
    top = float(score[i])
    # runner-up: the strongest score that is not this tempo or a simple multiple/fraction of it
    related = [bpm * r for r in (0.5, 2 / 3, 0.75, 1.0, 4 / 3, 1.5, 2.0)]
    other = max(
        (
            float(score[j])
            for j, b in enumerate(grid)
            if not any(abs(b - r) / r < 0.06 for r in related)
        ),
        default=0.0,
    )
    confidence = max(0.0, min(1.0, (top - other) / (top + 1e-9) * 2.0)) if top > 0 else 0.0
    confidence *= max(0.0, min(1.0, float(comb[i]) / 0.25))
    alternatives = []
    for r in (0.5, 2.0):
        b = bpm * r
        if BPM_RANGE[0] <= b <= BPM_RANGE[1] and float(np.interp(b, grid, comb)) >= 0.6 * float(
            comb[i]
        ):
            alternatives.append(float(b))
    # 3:2 and 4:3 faster readings: take one only when its beat grid clearly lands better
    base = _beat_strength(env_raw, bpm, fps)
    best_b, best_p = bpm, base
    for q in (1.5, 4 / 3):
        b = bpm * q
        if b > BPM_RANGE[1]:
            continue
        p = _beat_strength(env_raw, b, fps)
        if p > SWITCH * base and p > best_p:
            best_b, best_p = b, p
    if best_b != bpm:
        alternatives = [bpm] + [a for a in alternatives if abs(a - best_b) / best_b > 0.05]
        bpm = best_b
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
def key_features(y: Any) -> dict[str, Any]:
    """Everything the key decision uses: the best and second-best key, the correlations, and how tonal the audio is (harmonic share of the energy and how peaked the pitch-class profile is)."""
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
    p = profile / (profile.sum() + 1e-12)
    entropy = float(
        -(p * np.log2(p + 1e-12)).sum() / np.log2(12)
    )  # 1 = every pitch class equally present, lower = a few dominate
    e_total = float(np.mean(y**2)) + 1e-12
    return {
        "key": scores[0][1],
        "runner_up": scores[1][1],
        "r1": scores[0][0],
        "r2": scores[1][0],
        "harmonic_share": float(np.mean(harmonic**2)) / e_total,
        "entropy": entropy,
    }


def key_of(y: Any) -> tuple[str, float, str]:
    """(key like 'A minor', confidence 0-1, runner-up key). Confidence is 0 when the audio is not tonal enough to name a key at all (see key_is_clear)."""
    f = key_features(y)
    if f["harmonic_share"] < MIN_HARMONIC_SHARE:
        raise AudioProblem("No clear key: this is mostly drums or other unpitched sound.")
    confidence = max(0.0, min(1.0, (f["r1"] - f["r2"]) / 0.12)) * max(
        0.0, min(1.0, (f["r1"] - 0.2) / 0.5)
    )
    return f["key"], round(confidence, 2), f["runner_up"]


# ---- the whole thing -----------------------------------------------------------------------------------------------------------------------------------------
def _filename_bpm(p: Path) -> int | None:
    """The BPM written in the file name (CHZN1_Mango_bpm94), if any: shown beside the measured one, never used to correct it."""
    from julius.music import library

    return library.parse_name(p.stem)[0]


def bpm_agrees(a: Analysis) -> bool:
    """True when there is no filename BPM or it is within 2% of the measured one."""
    return a.filename_bpm is None or abs(a.bpm - a.filename_bpm) / a.filename_bpm <= 0.02


def analyze(path: str | Path) -> Analysis:
    p = check_path(path)
    y, total, seconds = load(p)
    if seconds < 3.0:
        raise AudioProblem(f"{p.name} is too short to find a tempo or key (under 3 seconds).")
    bpm, bpm_conf, alts = tempo(y)
    note = ""
    key_clear = True
    try:
        key, key_conf, runner = key_of(y)
    except AudioProblem:
        key, key_conf, runner = "", 0.0, ""
        key_clear = False
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
        key_clear=key_clear,
        filename_bpm=_filename_bpm(p),
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
            a.key or ("no clear key" if not a.key_clear else "not found"),
            f"{level(a.key_confidence)} ({a.key_confidence:.2f})" if a.key else "",
        ],
        ["Duration", clock(a.duration), ""],
    ]
    if a.filename_bpm is not None:
        rows.insert(
            1, ["Filename says", f"{a.filename_bpm} BPM", "agrees" if bpm_agrees(a) else "differs"]
        )
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
        else " No clear key: it is mostly drums or other unpitched sound."
        if not a.key_clear
        else " I couldn't find a key."
    )
    name_bpm = (
        f" The file name says {a.filename_bpm} BPM; the measured tempo is {a.bpm:g}. Give both and don't pick one."
        if not bpm_agrees(a)
        else f" That matches the {a.filename_bpm} BPM in the file name."
        if a.filename_bpm is not None
        else ""
    )
    if a.key and a.key_confidence < 0.66 and a.key_runner_up:
        key += f" It might also be {a.key_runner_up}."
    return (
        f"{a.name}: about {a.bpm:g} BPM ({level(a.bpm_confidence)} confidence).{alt}{name_bpm}{key} Length {clock(a.duration)}."
        + (f" {a.note}" if a.note and a.key_clear else "")
    )
