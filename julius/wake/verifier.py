"""Personal speaker verifier for the wake word (optional layer; the shipped detector works without it).

A logistic-regression model on the 16 x 96 openWakeWord features of the window that made stage 1 fire (the same idea as openWakeWord's
custom verifier). Positives: your own wake recordings. Negatives: your ordinary talk, Julius's own voice, other synthetic voices saying
"Julius", sound-alikes and background noise. It is trained on your voice, so it is stored OUTSIDE the repo (default ~/.julius/wake_verifier.pkl,
setting [wake] verifier in julius.toml) and never committed.

  python -m julius.wake.verifier train [--holdout wake2] [--out PATH]    --holdout leaves a wake session (and talk3, always) out of training
  python -m julius.wake.verifier info
Training sessions used: wake, wake2, quick, talk1, talk2, talk4 (when recorded). Never: talk3, wake3 and wake4 (the final eval sets)."""

import argparse
import pickle
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from julius import identity
from julius.paths import REPO_ROOT

ROOT = REPO_ROOT
DEFAULT = Path.home() / ".julius" / "wake_verifier.pkl"
REAL = ROOT / "diagnostics" / "wake_real"
DATA = ROOT / "wake" / "data"
SR, FRAME = 16000, 1280


def windows(model: Any, name: str, audio: Any, min_score: Any, every: Any | None = None) -> Any:
    """16 x 96 feature windows at frames whose detector score >= min_score (and, for background, every `every` frames)."""
    model.reset()
    x = np.concatenate(
        [np.zeros(int(0.5 * SR), np.float32), audio, np.zeros(int(0.6 * SR), np.float32)]
    )
    x = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    out = []
    for k, i in enumerate(range(0, len(x) - FRAME + 1, FRAME)):
        s = float(model.predict(x[i : i + FRAME])[name])
        if s >= min_score or (every and k % every == 0 and k > 16):
            out.append(model.preprocessor.get_features(model.model_inputs[name]).copy())
    return out


def load(path: str | Path) -> Any:
    a, sr = sf.read(path, dtype="float32")
    return a if a.ndim == 1 else a[:, 0]


def train(
    out: Callable[[str], Any],
    holdout: Any | None = None,
    min_score: float = 0.05,
    seed: int = 0,
    sessions: str = "wake,wake2,quick",
    model: Any | None = None,
) -> None:
    import build_dataset as bd
    from openwakeword.model import Model
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    rng = np.random.default_rng(seed)
    model_path = (
        Path(model)
        if model
        else ROOT / "julius" / "wake" / "models" / f"{identity.current().model_stem}_starter.onnx"
    )
    m = Model(wakeword_models=[str(model_path)], inference_framework="onnx")
    name = list(m.models)[0]
    pos, neg, count = [], [], {}
    wake_sessions = [s for s in sessions.split(",") if s != holdout and (REAL / s).is_dir()]
    for s in wake_sessions:
        for p in sorted((REAL / s).glob(f"{s}_*.wav")):
            a = load(p)
            for k in range(8):  # light augmentation so the verifier does not memorise a recording
                y = a if k == 0 else bd.augment(a, rng)[int(0.2 * SR) :]
                pos += windows(m, name, y, 0.05)
    count["positives (your wake recordings)"] = len(pos)
    for s in [s for s in ("talk1", "talk2", "talk4") if (REAL / s).is_dir()]:
        for p in sorted((REAL / s).glob(f"{s}_*.wav")):
            neg += windows(m, name, load(p), min_score, every=6)
    count["negatives: your talk"] = len(neg)
    len(neg)

    def sample(sub: Any, n: int, label: str, every: Any | None = None) -> Any:
        files = sorted((DATA / sub).glob("*.wav"))
        rng.shuffle(files)
        got = []
        for p in files[:n]:
            got += windows(m, name, load(p), min_score, every=every)
        count[label] = len(got)
        return got

    neg += sample("neg_julius", 400, "negatives: Julius's own voice")
    neg += [w for w in sample("pos", 700, "negatives: other voices saying Julius") if True]
    neg += sample("neg_hard", 300, "negatives: sound-alikes")
    neg += sample("neg_general", 150, "negatives: general speech", every=6)
    X = np.stack(pos + neg).astype(np.float32)
    y = np.array([1] * len(pos) + [0] * len(neg))
    # positives of the owner count for as much as all the negatives together
    clf = LogisticRegression(max_iter=3000, C=0.003, class_weight="balanced", random_state=0)
    pipe = make_pipeline(StandardScaler(), clf)  # input: windows flattened to 1536 values
    X = X.reshape(len(X), -1)
    pipe.fit(X, y)
    out = Path(out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pickle.dumps(pipe))
    p = pipe.predict_proba(X)[:, 1]
    print(count)
    print(
        f"train accuracy: positives kept {100 * (p[y == 1] >= 0.5).mean():.1f}%, negatives rejected {100 * (p[y == 0] < 0.5).mean():.1f}%"
    )
    print(
        "wrote",
        out,
        f"({out.stat().st_size / 1024:.0f} KB)",
        "| holdout:",
        holdout or "none",
        "| wake sessions:",
        wake_sessions,
    )


def load_verifier(path: str | Path | None = None) -> Any:
    path = Path(path).expanduser() if path else DEFAULT
    return pickle.load(open(path, "rb")) if path.exists() else None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["train", "info"])
    ap.add_argument("--holdout")
    ap.add_argument("--out", default=str(DEFAULT))
    ap.add_argument("--sessions", default="wake,wake2,quick")
    ap.add_argument("--model")
    a = ap.parse_args()
    if a.cmd == "train":
        train(a.out, a.holdout, sessions=a.sessions, model=a.model)
    else:
        print(DEFAULT, "exists" if DEFAULT.exists() else "missing")
