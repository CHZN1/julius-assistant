"""Train the "Julius" wake-word head on top of openWakeWord's pretrained feature extractor and export julius/wake/models/julius.onnx.

Each clip is embedded (80 ms frames x 96 dims); the head scores every 16-frame window (1.28 s) and the clip score is the max over windows
(multiple-instance training: no word alignment needed, and "Julius, what's the weather" works like a bare "Julius").
Positives: wake/data/pos. Negatives: neg_hard, neg_general, neg_julius (Julius's own voices, weighted up) and real human speech from
~/datasets (CREMA_D, RAVDESS) plus synthetic noise. Validation holds out three voices (bf_emma, af_sky, bm_george) the head never trains on.
Your ordinary-talk sessions talk1 and talk2 (diagnostics/wake_real/) are negatives; talk3 and the 30 wake utterances are never trained on.
Usage: python -m julius.wake.train_head [--epochs 40]"""

import argparse
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch
import torch.nn as nn
from scipy import signal

from julius.paths import REPO_ROOT

ROOT = REPO_ROOT
DATA = ROOT / "wake" / "data"
OUT = ROOT / "julius" / "wake" / "models"
SR, WIN, DIM = 16000, 16, 96
HOLDOUT_VOICES = ("bf_emma", "af_sky", "bm_george")


class Head(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.net = nn.Sequential(
            nn.Flatten(),
            nn.Linear(WIN * DIM, 128),
            nn.LayerNorm(128),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Linear(64, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: Any) -> Any:
        return self.net(x)


def read16k(path: str | Path) -> Any:
    x, sr = sf.read(path, dtype="float32")
    if x.ndim > 1:
        x = x[:, 0]
    return (
        x
        if sr == SR
        else signal.resample_poly(x, SR // np.gcd(SR, sr), sr // np.gcd(SR, sr)).astype(np.float32)
    )


def embed(files: Any, cache: Any, extractor: Any, max_s: float = 4.0, loader: Any = read16k) -> Any:
    if cache.exists():
        return list(np.load(cache, allow_pickle=True))
    out = []
    for i in range(0, len(files), 64):
        clips = []
        for f in files[i : i + 64]:
            x = loader(f)[: int(max_s * SR)]
            x = np.pad(x, (0, int(max_s * SR) - len(x)))
            clips.append((np.clip(x, -1, 1) * 32767).astype(np.int16))
        e = extractor.embed_clips(np.stack(clips), batch_size=64)
        out += list(e)
        print(f"  embedded {min(i + 64, len(files))}/{len(files)}", flush=True)
    np.save(cache, np.array(out, dtype=object), allow_pickle=True)
    return out


def windows(emb: Any) -> Any:  # (frames, 96) -> (n, 16, 96)
    return np.stack([emb[i : i + WIN] for i in range(len(emb) - WIN + 1)]).astype(np.float32)


def clip_scores(model: Any, embs: Any) -> Any:
    model.eval()
    out = []
    with torch.no_grad():
        for e in embs:
            out.append(float(model(torch.from_numpy(windows(e))).max()))
    return np.array(out)


CLASS_WEIGHT = {
    "neg_talk": 3.0,
    "pos": 1.0,
    "neg_hard": 2.0,
    "neg_general": 1.0,
    "neg_julius": 4.0,
    "neg_real": 1.0,
    "neg_noise": 1.0,
}


def _synthetic_sets(args: list[str], extractor: Any, tag: Any, rng: np.random.Generator) -> Any:
    """The generated classes (pos, hard/general/julius negatives) plus real speech negatives."""
    manifest = {
        m["file"]: m
        for f in sorted(DATA.glob("manifest_*.json"))
        for m in json.loads(f.read_text())
    }
    sets = {}
    for cls in ("pos", "neg_hard", "neg_general", "neg_julius"):
        files = sorted(
            f
            for f in manifest
            if f.startswith(cls + "/") and not (args.public and Path(f).name.startswith("user_"))
        )
        embs = embed([DATA / f for f in files], DATA / f"emb_{cls}{tag}.npy", extractor)
        sets[cls] = [(manifest[f]["voice"], e) for f, e in zip(files, embs, strict=False)]
    real = sorted((Path.home() / "datasets" / "CREMA_D" / "AudioWAV").glob("*.wav")) + sorted(
        (Path.home() / "datasets" / "RAVDESS").rglob("*.wav")
    )
    rng.shuffle(real)
    real = real[: args.real_negatives]
    sets["neg_real"] = [("real", e) for e in embed(real, DATA / "emb_neg_real.npy", extractor)]
    return sets


def _talk_and_noise_sets(sets: Any, args: list[str], extractor: Any) -> None:
    """Ordinary talk (talk3 held out for the false-wake eval) and background-noise negatives."""
    noise = np.random.default_rng(0)

    def noise_clip(i: Any) -> Any:  # background noise negatives
        n = int(4 * SR)
        w = noise.standard_normal(n).astype(np.float32)
        if i % 3 == 1:
            f = np.fft.rfft(w)
            f /= np.sqrt(np.maximum(np.arange(len(f)), 1))
            w = np.fft.irfft(f, n).astype(np.float32)
        return w / np.abs(w).max() * noise.uniform(0.02, 0.5)

    sessions = (
        ()
        if args.public
        else (("talk1", "user"), ("talk2", "user"), ("talk4", "user"), ("talk3", "user_val"))
    )
    for sess, voice in sessions:
        tf = sorted((ROOT / "diagnostics" / "wake_real" / sess).glob(f"{sess}_*.wav"))
        sets.setdefault("neg_talk", []).extend(
            (voice, e) for e in embed(tf, DATA / f"emb_{sess}.npy", extractor)
        )
    sets["neg_noise"] = [
        ("noise", e)
        for e in embed(list(range(300)), DATA / "emb_neg_noise.npy", extractor, loader=noise_clip)
    ]


def _train(
    model: Any, train: Any, val: Any, sets: Any, epochs: int, rng: np.random.Generator
) -> None:
    """Weighted clip-level BCE training with a held-out report every five epochs."""
    opt = torch.optim.AdamW(model.parameters(), 2e-3, weight_decay=1e-3)
    bce = nn.BCELoss(reduction="none")
    torch.manual_seed(0)
    for ep in range(epochs):
        model.train()
        rng.shuffle(train)
        total = 0
        for i in range(0, len(train), 32):
            batch = train[i : i + 32]
            xs, y, w, ids = [], [], [], []
            for k, (cls, e) in enumerate(batch):
                wi = windows(e)
                xs.append(torch.from_numpy(wi))
                ids += [k] * len(wi)
                y.append(1.0 if cls == "pos" else 0.0)
                w.append(CLASS_WEIGHT[cls])
            s = model(torch.cat(xs)).squeeze(1)
            ids_t = torch.tensor(ids)
            clip = torch.stack([s[ids_t == k].max() for k in range(len(batch))])
            loss = (bce(clip, torch.tensor(y)) * torch.tensor(w)).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += float(loss)
        if ep % 5 == 4 or ep == epochs - 1:
            _report(model, val, sets, ep, total / (len(train) / 32))


def _report(model: Any, val: Any, sets: Any, ep: Any, loss: Any) -> None:
    scores = {
        c: clip_scores(model, [e for k, e in val if k == c])
        for c in sets
        if any(k == c for k, _ in val)
    }
    print(
        f"epoch {ep + 1} loss {loss:.4f} | held-out voice: "
        + ", ".join(
            f"{c} {'recall' if c == 'pos' else 'false-fire'} {(v >= 0.5).mean() * 100:.1f}%"
            for c, v in scores.items()
        ),
        flush=True,
    )


def main() -> None:
    """Train the wake head on the generated, real and recorded clips and write it as ONNX."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--real-negatives", type=int, default=4000)
    ap.add_argument(
        "--public",
        action="store_true",
        help="release build: no personal recordings (no pos/user_* clips, no diagnostics/wake_real talk sessions); writes julius_public.onnx next to the shipped model",
    )
    a = ap.parse_args()
    tag = "_public" if a.public else ""
    from openwakeword.utils import AudioFeatures

    extractor = AudioFeatures(inference_framework="onnx")
    rng = random.Random(3)
    sets = _synthetic_sets(a, extractor, tag, rng)
    _talk_and_noise_sets(sets, a, extractor)
    train, val = [], []
    for cls, items in sets.items():
        for voice, e in items:
            (val if voice in HOLDOUT_VOICES + ("user_val",) else train).append((cls, e))
    print(
        {c: sum(1 for k, _ in train if k == c) for c in sets},
        "| held-out voice clips:",
        len(val),
        flush=True,
    )
    model = Head()
    _train(model, train, val, sets, a.epochs, rng)
    OUT.mkdir(exist_ok=True)
    model.eval()
    torch.onnx.export(
        model,
        torch.zeros(1, WIN, DIM),
        OUT / f"julius{tag}.onnx",
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    torch.save(model.state_dict(), OUT / f"julius_head{tag}.pt")
    print("wrote", OUT / f"julius{tag}.onnx")


if __name__ == "__main__":
    main()
