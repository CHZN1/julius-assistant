"""Passive learning for the wake word: Julius gets better at YOUR voice from normal use. Everything stays on this Mac (~/.julius/wake_personal, never uploaded, never in git).

What is collected (by `Learner`, called from julius_wake.WakeEngine):
  positives   a wake the Whisper stage confirmed, and an earlier rejected fire that you repeated within 5 s ("Julius." ... "Julius!") once the repeat was accepted
  negatives   a rejected fire nobody repeated (Whisper heard something that is not Julius) - kept after the 5 s repeat window has passed
  setup       up to 5 clips from `python wake/personal.py setup` (optional, skippable)
When `learn_after` (default 20) new positives have arrived, a background process (`python wake/personal.py retrain`, low priority) fine-tunes the starter head on them,
then KEEPS the result only if it beats the current model on your own most recent clips (held out from that training run): more of them detected, and no
more false fires on your recent rejected clips. Otherwise the current model stays. The new model is picked up the next time the detector resets (after a turn).
Needs torch and onnx for the retrain step (pip install -r requirements-wake-learning.txt); without them clips are still collected and nothing else changes.

CLI: setup | status | retrain [--force] | forget (deletes every collected clip and the personal model)."""

import argparse
import contextlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from numpy import ndarray

from julius.paths import REPO_ROOT

SR = 16000
ROOT = REPO_ROOT
STARTER = ROOT / "julius" / "wake" / "models" / "julius_starter.onnx"
STARTER_HEAD = ROOT / "julius" / "wake" / "models" / "julius_starter_head.pt"
MAX_POS, MAX_NEG, MAX_SECONDS = 400, 200, 3.0
MIN_VALIDATION = 5


def home() -> Path:
    return Path(os.environ.get("JULIUS_WAKE_PERSONAL") or Path.home() / ".julius" / "wake_personal")


class Store:
    """Clips on disk plus a small state file. pos/ and neg/ hold 16 kHz mono wavs named <epoch-ms>_<kind>.wav; the oldest are dropped past the caps."""

    def __init__(self, folder: Path | None = None) -> None:
        self.dir = Path(folder or home())
        self.pos, self.neg = self.dir / "pos", self.dir / "neg"
        self.state_path = self.dir / "state.json"

    def _write(self, folder: Path, kind: str, clip: ndarray, when: int | float) -> Path:
        import soundfile as sf

        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        x = np.asarray(clip, dtype=np.float32).reshape(-1)[: int(MAX_SECONDS * SR)]
        stamp = int(when * 1000)
        while (folder / f"{stamp}_{kind}.wav").exists():
            stamp += 1  # clips arriving in the same millisecond keep distinct names
        path = folder / f"{stamp}_{kind}.wav"
        sf.write(path, x, SR, subtype="PCM_16")
        os.chmod(path, 0o600)
        cap = MAX_POS if folder == self.pos else MAX_NEG
        for old in sorted(folder.glob("*.wav"))[:-cap]:
            old.unlink()
        return path

    def add_positive(self, clip: ndarray, kind: str, when: int | float | None = None) -> Path:
        return self._write(self.pos, kind, clip, when or time.time())

    def add_negative(self, clip: ndarray, when: int | float | None = None) -> Path:
        return self._write(self.neg, "rejected", clip, when or time.time())

    def positives(self) -> list[Path | Any]:
        return sorted(self.pos.glob("*.wav")) if self.pos.exists() else []

    def negatives(self) -> list[Path | Any]:
        return sorted(self.neg.glob("*.wav")) if self.neg.exists() else []

    def state(self) -> dict[str, int]:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def save_state(self, **fields) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        s = {**self.state(), **fields}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=1))
        os.replace(tmp, self.state_path)

    @property
    def model(self) -> Path:
        return self.dir / "model.onnx"

    def new_positives(self) -> int:
        return max(0, len(self.positives()) - int(self.state().get("positives_at_last_train", 0)))

    def status(self) -> dict[str, int | bool | None]:
        """A plain dict for the terminal and for a UI that wants to show progress."""
        s = self.state()
        need = int(s.get("learn_after", 20))
        return {
            "positives": len(self.positives()),
            "negatives": len(self.negatives()),
            "new_since_last_training": self.new_positives(),
            "needed_for_next_training": need,
            "personal_model": self.model.exists(),
            "last_training": s.get("last_result"),
            "training_now": (self.dir / "training.lock").exists(),
        }

    def forget(self) -> None:
        import shutil

        if self.dir.exists():
            shutil.rmtree(self.dir)


class Learner:
    """Turns wake events into training clips. `spawn()` starts the background retrain (injected in tests)."""

    def __init__(
        self,
        store: Store | None = None,
        learn_after: int = 20,
        spawn: Callable | None = None,
        clock: Any | Callable = time.time,
        repeat_window: float = 5.0,
    ) -> None:
        self.store, self.learn_after, self.clock, self.repeat_window = (
            store or Store(),
            int(learn_after),
            clock,
            repeat_window,
        )
        self.spawn = spawn or spawn_retrain
        self.pending = None  # a rejected fire waiting out the repeat window: {"time", "clip"}

    def observe(self, event: dict[str, bool | str | ndarray | None]) -> None:
        """event: the dict WakeStream decided ({"accepted", "clip", "reason"...}). Never raises: learning must not break waking."""
        with contextlib.suppress(Exception):
            self._observe(event)

    def _observe(self, event: dict[str, bool | str | ndarray | None]) -> None:
        now = self.clock()
        self.flush(now)
        clip = event.get("clip")
        if clip is None:
            return
        if event.get("accepted"):
            self.store.add_positive(clip, "confirmed", now)
            if self.pending is not None and now - self.pending["time"] <= self.repeat_window:
                self.store.add_positive(
                    self.pending["clip"], "repeat_after_reject", self.pending["time"]
                )  # the first try was a real wake that was missed
            self.pending = None
            self.maybe_retrain()
        elif (
            event.get("reason") == "whisper"
        ):  # the speaker-verifier rejections are a different problem (someone else's voice), not training data
            if self.pending is not None:
                self.store.add_negative(self.pending["clip"], self.pending["time"])
            self.pending = {"time": now, "clip": clip}

    def flush(self, now: float | None = None) -> None:
        """Call from time to time: a rejected fire nobody repeated within the window becomes a negative."""
        now = now or self.clock()
        if self.pending is not None and now - self.pending["time"] > self.repeat_window:
            self.store.add_negative(self.pending["clip"], self.pending["time"])
            self.pending = None

    def maybe_retrain(self) -> None:
        if (
            self.store.new_positives() >= self.learn_after
            and not (self.store.dir / "training.lock").exists()
        ):
            self.store.save_state(learn_after=self.learn_after)
            self.spawn(self.store)


def spawn_retrain(store: Any) -> None:
    """Start `retrain` as a detached, low-priority process. Failure to start is silent: the next positive tries again."""
    with contextlib.suppress(OSError):
        subprocess.Popen(
            [
                "nice",
                "-n",
                "15",
                sys.executable,
                str(Path(__file__).resolve()),
                "retrain",
                "--dir",
                str(store.dir),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


# ---------------- retraining (torch + onnx) ----------------


def decide(
    current: dict[str, float],
    candidate: dict[str, float],
    recall_gain: float = 0.05,
    extra_false: float = 0.15,
    false_ceiling: float = 0.30,
) -> Any:
    """Keep the candidate only if it finds clearly more of the user's recent wakes (a miss costs a repeated "Julius"; an extra false fire only costs one Whisper check),
    without a big rise in false fires on their recent rejected clips; or the same wakes with fewer false fires.
    current/candidate: {"recall": 0..1, "false_rate": 0..1} measured at the stage-1 threshold."""
    c_false, n_false = current["false_rate"], candidate["false_rate"]
    if candidate["recall"] >= current["recall"] + recall_gain:
        if n_false <= max(c_false + extra_false, false_ceiling):
            return True, "finds more of your recent wakes"
        return False, "finds more wakes but adds too many false fires on your recent rejected clips"
    if candidate["recall"] >= current["recall"] and n_false < c_false:
        return True, "same wakes, fewer false fires"
    return False, "does not beat the current model on your recent clips"


def _augment(x: Any, rng: np.random.Generator) -> Any:
    """Light, dependency-free variety: gain (quiet and far takes), a small room, noise, and a little speed change."""
    from scipy import signal

    if rng.random() < 0.35:
        r = rng.uniform(0.9, 1.1)
        x = signal.resample_poly(x, 100, int(round(100 * r))).astype(np.float32)
    if rng.random() < 0.4:
        n = int(rng.uniform(0.06, 0.35) * SR)
        ir = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n)
        ir[0] = 1
        x = signal.fftconvolve(x, ir / np.abs(ir).sum() * 2.5)[: len(x) + n // 2].astype(np.float32)
    x = x / (np.abs(x).max() + 1e-6) * float(np.exp(rng.uniform(np.log(0.04), np.log(0.9))))
    if rng.random() < 0.8:
        nz = rng.standard_normal(len(x)).astype(np.float32)
        x = x + nz / (nz.std() + 1e-9) * x.std() / (10 ** (rng.uniform(6, 30) / 20))
    lead = np.zeros(int(rng.uniform(0.2, 0.9) * SR), np.float32)
    return np.clip(np.concatenate([lead, x, np.zeros(int(0.5 * SR), np.float32)]), -1, 1)


def stream_windows(model: Any, name: str, audio: Any, lead: float = 0.5, tail: float = 0.8) -> Any:
    """The 16 x 96 feature windows the live detector sees while this clip streams past it (one per 80 ms frame once 16 frames exist). Shape (n, 16, 96)."""
    model.reset()
    x = np.concatenate(
        [
            np.zeros(int(lead * SR), np.float32),
            np.asarray(audio, np.float32),
            np.zeros(int(tail * SR), np.float32),
        ]
    )
    x = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    out = []
    for k, i in enumerate(range(0, len(x) - 1280 + 1, 1280)):
        model.predict(x[i : i + 1280])
        if k >= 16:
            out.append(
                np.asarray(
                    model.preprocessor.get_features(model.model_inputs[name]), dtype=np.float32
                )
                .reshape(16, 96)
                .copy()
            )
    return np.stack(out) if out else np.zeros((0, 16, 96), np.float32)


def _read(path: str | Path) -> Any:
    import soundfile as sf

    x, sr = sf.read(path, dtype="float32")
    x = x if x.ndim == 1 else x[:, 0]
    if sr != SR:
        from math import gcd

        from scipy import signal

        g = gcd(SR, sr)
        x = signal.resample_poly(x, SR // g, sr // g).astype(np.float32)
    return x


def _noise_clips(rng: np.random.Generator, count: int = 150) -> Any:
    """White and pink-ish noise at random levels: hard negatives for the head."""
    noise = []
    for i in range(count):
        w = rng.standard_normal(4 * SR).astype(np.float32)
        if i % 3 == 1:
            f = np.fft.rfft(w)
            f /= np.sqrt(np.maximum(np.arange(len(f)), 1))
            w = np.fft.irfft(f, 4 * SR).astype(np.float32)
        noise.append(w / np.abs(w).max() * rng.uniform(0.02, 0.5))
    return noise


def _training_data(train_pos: Any, train_neg: Any, rng: np.random.Generator, emb: Any) -> Any:
    """(embedding windows, label, weight) rows: augmented positives, rejected clips, noise."""
    tp = [_read(p) for p in train_pos]
    aug = [_augment(x, rng) for x in tp for _ in range(24)] + [x for x in tp]
    tn = [_read(p) for p in train_neg]
    aug_neg = [_augment(x, rng) for x in tn for _ in range(6)] + tn
    noise = _noise_clips(rng)
    e_pos, e_neg, e_noise = emb(aug), emb(aug_neg) if aug_neg else [], emb(noise)
    return (
        [(e, 1.0, 1.0) for e in e_pos]
        + [(e, 0.0, 2.0) for e in e_neg]
        + [(e, 0.0, 1.0) for e in e_noise]
    )


def _fit(model: Any, data: Any, epochs: int, seed: Any) -> None:
    """Fine-tune the head with a clip-level (max over windows) weighted BCE loss."""
    import torch
    import torch.nn as nn

    opt = torch.optim.AdamW(model.parameters(), 3e-4, weight_decay=1e-3)
    bce = nn.BCELoss(reduction="none")
    order = np.random.default_rng(seed)
    for _ in range(epochs):
        model.train()
        order.shuffle(data)
        for i in range(0, len(data), 32):
            batch = data[i : i + 32]
            xs, ids, y, w = [], [], [], []
            for k, (e, label, weight) in enumerate(batch):
                xs.append(torch.from_numpy(e))
                ids += [k] * len(e)
                y.append(label)
                w.append(weight)
            s = model(torch.cat(xs)).squeeze(1)
            ids_t = torch.tensor(ids)
            clip = torch.stack([s[ids_t == k].max() for k in range(len(batch))])
            loss = (bce(clip, torch.tensor(y)) * torch.tensor(w)).mean()
            opt.zero_grad()
            loss.backward()
            opt.step()
    model.eval()


def _clip_scores(model: Any, wins: Any) -> Any:
    import torch

    with torch.no_grad():
        return np.array([float(model(torch.from_numpy(w)).max()) if len(w) else 0.0 for w in wins])


def _judge(model: Any, val_pos_windows: Any, val_neg_windows: Any, threshold: float) -> Any:
    recall = (_clip_scores(model, val_pos_windows) >= threshold).mean()
    false = (_clip_scores(model, val_neg_windows) >= threshold).mean() if val_neg_windows else 0.0
    return {"recall": float(recall), "false_rate": float(false)}


def _current_head(store: Any, starter_head: Any, Head: Any) -> Any:
    """The personal head if there is one, otherwise the starter."""
    import torch

    head = Head()
    personal = store.dir / "head.pt"
    source = personal if store.model.exists() and personal.exists() else starter_head
    head.load_state_dict(torch.load(source, map_location="cpu"))
    head.eval()
    return head


def _export(model: Any, store: Any) -> None:
    """Write the kept head as ONNX (model.onnx) plus its weights (head.pt)."""
    import torch

    tmp = store.dir / "model.tmp.onnx"
    torch.onnx.export(
        model,
        torch.zeros(1, 16, 96),
        tmp,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    torch.save(model.state_dict(), store.dir / "head.pt")
    os.replace(tmp, store.model)


def _split_clips(pos: Any, neg: Any) -> Any:
    """Hold out the newest quarter of the positives (and of the negatives) to judge the result."""
    n_val = min(max(MIN_VALIDATION, len(pos) // 4), len(pos) // 2)
    nv = max(0, min(len(neg) // 4, len(neg) - 1)) if len(neg) >= 4 else 0
    return pos[:-n_val], pos[-n_val:], neg[: len(neg) - nv], (neg[-nv:] if nv else [])


def _train_and_judge(
    store: Any,
    pos: Any,
    neg: Any,
    epochs: int,
    seed: Any,
    starter: Any,
    starter_head: Any,
    threshold: float,
    Head: Any,
) -> Any:
    """Train a candidate head and compare it with the current one; returns (result, candidate)."""
    import torch
    from openwakeword.model import Model

    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    train_pos, val_pos, train_neg, val_neg = _split_clips(pos, neg)  # the NEWEST clips judge it
    feat = Model(wakeword_models=[str(starter)], inference_framework="onnx")
    fname = list(feat.models)[0]

    def emb(clips: Any) -> Any:
        # features exactly as the live detector computes them (clip-at-once embedding differs)
        return [stream_windows(feat, fname, c) for c in clips]

    data = _training_data(train_pos, train_neg, rng, emb)
    model = Head()
    model.load_state_dict(torch.load(starter_head, map_location="cpu"))
    _fit(model, data, epochs, seed)
    # judged on the held-out recent clips only
    vp = emb([_read(p) for p in val_pos])
    vn = emb([_read(p) for p in val_neg]) if val_neg else []
    cur = _judge(_current_head(store, starter_head, Head), vp, vn, threshold)
    cand = _judge(model, vp, vn, threshold)
    kept, why = decide(cur, cand)
    result = {
        "kept": kept,
        "reason": why,
        "current": cur,
        "candidate": cand,
        "validation_positives": len(val_pos),
        "validation_negatives": len(val_neg),
        "trained_on": len(train_pos),
        "when": time.strftime("%Y-%m-%d %H:%M"),
    }
    return result, model


def retrain(
    store: Store | None = None,
    force: bool = False,
    epochs: int = 15,
    seed: int = 0,
    log: Callable = print,
    starter: None = None,
    starter_head: None = None,
    threshold: float = 0.0005,
) -> dict[str, str | bool]:
    """Fine-tune the starter head on this user's clips and keep it only if it wins on their most recent held-out clips. Returns a result dict (also stored in state.json)."""
    store = store or Store()
    starter = Path(starter or STARTER)
    starter_head = Path(starter_head or STARTER_HEAD)
    lock = store.dir / "training.lock"
    try:
        import torch  # noqa: F401
        from openwakeword.model import Model  # noqa: F401

        sys.path.insert(0, str(ROOT))
        from julius.wake.train_head import Head
    except ImportError as e:
        return {
            "kept": False,
            "reason": f"training needs torch and onnx ({e.name}); clips are still being collected",
        }
    pos, neg = store.positives(), store.negatives()
    if len(pos) < 2 * MIN_VALIDATION and not force:
        return {"kept": False, "reason": f"only {len(pos)} clips so far"}
    store.dir.mkdir(parents=True, exist_ok=True)
    if lock.exists() and time.time() - lock.stat().st_mtime < 3 * 3600:
        return {"kept": False, "reason": "already training"}
    lock.write_text(str(os.getpid()))
    try:
        result, model = _train_and_judge(
            store, pos, neg, epochs, seed, starter, starter_head, threshold, Head
        )
        if result["kept"]:
            _export(model, store)
        store.save_state(positives_at_last_train=len(pos), last_result=result)
        log(json.dumps(result))
        return result
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()


# ---------------- optional quick setup ----------------


def record_clip(seconds: float = 2.5, device: Any | None = None) -> Any:
    import sounddevice as sd

    x = sd.rec(int(seconds * SR), samplerate=SR, channels=1, dtype="float32", device=device)
    sd.wait()
    return x[:, 0]


def setup(
    store: Store | None = None,
    count: int = 5,
    record: Callable = record_clip,
    ask: Callable = input,
    say: Callable = print,
) -> int:
    """Optional: record up to `count` clips of you saying "Julius". Press Enter to record each; type s to skip the rest. A UI can call the same function with its own prompts."""
    store = store or Store()
    done = 0
    say(
        f'Quick setup: say "Julius" (or "Hey Julius") naturally, up to {count} times. Enter = record, s = skip the rest. You can skip this entirely; Julius also learns from normal use.'
    )
    for i in range(count):
        if ask(f"  [{i + 1}/{count}] Enter to record, s to skip: ").strip().lower().startswith("s"):
            break
        clip = record()
        if float(np.abs(clip).max()) < 0.01:
            say("  that was too quiet, skipping this one")
            continue
        store.add_positive(np.concatenate([np.zeros(int(0.3 * SR), np.float32), clip]), "setup")
        done += 1
    say(
        f"Saved {done} clip(s). {store.status()['positives']} in total; training starts after {store.state().get('learn_after', 20)} new ones."
    )
    return done


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["setup", "status", "retrain", "forget"])
    ap.add_argument("--dir")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    store = Store(a.dir)
    if a.cmd == "setup":
        setup(store)
    elif a.cmd == "status":
        print(json.dumps(store.status(), indent=1))
    elif a.cmd == "retrain":
        print(json.dumps(retrain(store, force=a.force), indent=1))
    else:
        if (
            a.yes
            or input(
                f"Delete every collected wake clip and the personal model in {store.dir}? Type yes: "
            )
            .strip()
            .lower()
            == "yes"
        ):
            store.forget()
            print("deleted")
        else:
            print("kept")


if __name__ == "__main__":
    main()
