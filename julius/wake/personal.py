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
from julius.paths import REPO_ROOT
import argparse, json, os, subprocess, sys, tempfile, time
from pathlib import Path
import numpy as np

SR = 16000
ROOT = REPO_ROOT
STARTER = ROOT / "julius" / "wake" / "models" / "julius_starter.onnx"
STARTER_HEAD = ROOT / "julius" / "wake" / "models" / "julius_starter_head.pt"
MAX_POS, MAX_NEG, MAX_SECONDS = 400, 200, 3.0
MIN_VALIDATION = 5


def home():
    return Path(os.environ.get("JULIUS_WAKE_PERSONAL") or Path.home() / ".julius" / "wake_personal")


class Store:
    """Clips on disk plus a small state file. pos/ and neg/ hold 16 kHz mono wavs named <epoch-ms>_<kind>.wav; the oldest are dropped past the caps."""
    def __init__(self, folder=None):
        self.dir = Path(folder or home()); self.pos, self.neg = self.dir / "pos", self.dir / "neg"; self.state_path = self.dir / "state.json"

    def _write(self, folder, kind, clip, when):
        import soundfile as sf
        folder.mkdir(parents=True, exist_ok=True); os.chmod(self.dir, 0o700)
        x = np.asarray(clip, dtype=np.float32).reshape(-1)[: int(MAX_SECONDS * SR)]
        stamp = int(when * 1000)
        while (folder / f"{stamp}_{kind}.wav").exists(): stamp += 1   # clips arriving in the same millisecond keep distinct names
        path = folder / f"{stamp}_{kind}.wav"; sf.write(path, x, SR, subtype="PCM_16"); os.chmod(path, 0o600)
        cap = MAX_POS if folder == self.pos else MAX_NEG
        for old in sorted(folder.glob("*.wav"))[:-cap]: old.unlink()
        return path

    def add_positive(self, clip, kind, when=None): return self._write(self.pos, kind, clip, when or time.time())
    def add_negative(self, clip, when=None): return self._write(self.neg, "rejected", clip, when or time.time())
    def positives(self): return sorted(self.pos.glob("*.wav")) if self.pos.exists() else []
    def negatives(self): return sorted(self.neg.glob("*.wav")) if self.neg.exists() else []

    def state(self):
        try: return json.loads(self.state_path.read_text())
        except (OSError, ValueError): return {}

    def save_state(self, **fields):
        self.dir.mkdir(parents=True, exist_ok=True); s = {**self.state(), **fields}
        tmp = self.state_path.with_suffix(".tmp"); tmp.write_text(json.dumps(s, indent=1)); os.replace(tmp, self.state_path)

    @property
    def model(self): return self.dir / "model.onnx"

    def new_positives(self):
        return max(0, len(self.positives()) - int(self.state().get("positives_at_last_train", 0)))

    def status(self):
        """A plain dict for the terminal and for a UI that wants to show progress."""
        s = self.state(); need = int(s.get("learn_after", 20))
        return {"positives": len(self.positives()), "negatives": len(self.negatives()), "new_since_last_training": self.new_positives(), "needed_for_next_training": need,
                "personal_model": self.model.exists(), "last_training": s.get("last_result"), "training_now": (self.dir / "training.lock").exists()}

    def forget(self):
        import shutil
        if self.dir.exists(): shutil.rmtree(self.dir)


class Learner:
    """Turns wake events into training clips. `spawn()` starts the background retrain (injected in tests)."""
    def __init__(self, store=None, learn_after=20, spawn=None, clock=time.time, repeat_window=5.0):
        self.store, self.learn_after, self.clock, self.repeat_window = store or Store(), int(learn_after), clock, repeat_window
        self.spawn = spawn or spawn_retrain; self.pending = None   # a rejected fire waiting out the repeat window: {"time", "clip"}

    def observe(self, event):
        """event: the dict WakeStream decided ({"accepted", "clip", "reason"...}). Never raises: learning must not break waking."""
        try: self._observe(event)
        except Exception: pass

    def _observe(self, event):
        now = self.clock(); self.flush(now); clip = event.get("clip")
        if clip is None: return
        if event.get("accepted"):
            self.store.add_positive(clip, "confirmed", now)
            if self.pending is not None and now - self.pending["time"] <= self.repeat_window:
                self.store.add_positive(self.pending["clip"], "repeat_after_reject", self.pending["time"])   # the first try was a real wake that was missed
            self.pending = None
            self.maybe_retrain()
        elif event.get("reason") == "whisper":   # the speaker-verifier rejections are a different problem (someone else's voice), not training data
            if self.pending is not None: self.store.add_negative(self.pending["clip"], self.pending["time"])
            self.pending = {"time": now, "clip": clip}

    def flush(self, now=None):
        """Call from time to time: a rejected fire nobody repeated within the window becomes a negative."""
        now = now or self.clock()
        if self.pending is not None and now - self.pending["time"] > self.repeat_window:
            self.store.add_negative(self.pending["clip"], self.pending["time"]); self.pending = None

    def maybe_retrain(self):
        if self.store.new_positives() >= self.learn_after and not (self.store.dir / "training.lock").exists():
            self.store.save_state(learn_after=self.learn_after); self.spawn(self.store)


def spawn_retrain(store):
    """Start `retrain` as a detached, low-priority process. Failure to start is silent: the next positive tries again."""
    try:
        subprocess.Popen(["nice", "-n", "15", sys.executable, str(Path(__file__).resolve()), "retrain", "--dir", str(store.dir)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError: pass


# ---------------- retraining (torch + onnx) ----------------

def decide(current, candidate, recall_gain=0.05, extra_false=0.15, false_ceiling=0.30):
    """Keep the candidate only if it finds clearly more of the user's recent wakes (a miss costs a repeated "Julius"; an extra false fire only costs one Whisper check),
    without a big rise in false fires on their recent rejected clips; or the same wakes with fewer false fires.
    current/candidate: {"recall": 0..1, "false_rate": 0..1} measured at the stage-1 threshold."""
    c_false, n_false = current["false_rate"], candidate["false_rate"]
    if candidate["recall"] >= current["recall"] + recall_gain:
        if n_false <= max(c_false + extra_false, false_ceiling): return True, "finds more of your recent wakes"
        return False, "finds more wakes but adds too many false fires on your recent rejected clips"
    if candidate["recall"] >= current["recall"] and n_false < c_false: return True, "same wakes, fewer false fires"
    return False, "does not beat the current model on your recent clips"


def _augment(x, rng):
    """Light, dependency-free variety: gain (quiet and far takes), a small room, noise, and a little speed change."""
    from scipy import signal
    if rng.random() < .35:
        r = rng.uniform(.9, 1.1); x = signal.resample_poly(x, 100, int(round(100 * r))).astype(np.float32)
    if rng.random() < .4:
        n = int(rng.uniform(.06, .35) * SR); ir = rng.standard_normal(n) * np.exp(-6.9 * np.arange(n) / n); ir[0] = 1
        x = signal.fftconvolve(x, ir / np.abs(ir).sum() * 2.5)[: len(x) + n // 2].astype(np.float32)
    x = x / (np.abs(x).max() + 1e-6) * float(np.exp(rng.uniform(np.log(.04), np.log(.9))))
    if rng.random() < .8:
        nz = rng.standard_normal(len(x)).astype(np.float32); x = x + nz / (nz.std() + 1e-9) * x.std() / (10 ** (rng.uniform(6, 30) / 20))
    lead = np.zeros(int(rng.uniform(.2, .9) * SR), np.float32)
    return np.clip(np.concatenate([lead, x, np.zeros(int(.5 * SR), np.float32)]), -1, 1)


def stream_windows(model, name, audio, lead=0.5, tail=0.8):
    """The 16 x 96 feature windows the live detector sees while this clip streams past it (one per 80 ms frame once 16 frames exist). Shape (n, 16, 96)."""
    model.reset(); x = np.concatenate([np.zeros(int(lead * SR), np.float32), np.asarray(audio, np.float32), np.zeros(int(tail * SR), np.float32)])
    x = (np.clip(x, -1, 1) * 32767).astype(np.int16); out = []
    for k, i in enumerate(range(0, len(x) - 1280 + 1, 1280)):
        model.predict(x[i:i + 1280])
        if k >= 16: out.append(np.asarray(model.preprocessor.get_features(model.model_inputs[name]), dtype=np.float32).reshape(16, 96).copy())
    return np.stack(out) if out else np.zeros((0, 16, 96), np.float32)


def _read(path):
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32"); x = x if x.ndim == 1 else x[:, 0]
    if sr != SR:
        from scipy import signal
        from math import gcd
        g = gcd(SR, sr); x = signal.resample_poly(x, SR // g, sr // g).astype(np.float32)
    return x


def retrain(store=None, force=False, epochs=15, seed=0, log=print, starter=None, starter_head=None, threshold=0.0005):
    """Fine-tune the starter head on this user's clips and keep it only if it wins on their most recent held-out clips. Returns a result dict (also stored in state.json)."""
    store = store or Store(); starter = Path(starter or STARTER); starter_head = Path(starter_head or STARTER_HEAD)
    lock = store.dir / "training.lock"
    try:
        import torch, torch.nn as nn
        from openwakeword.model import Model
        sys.path.insert(0, str(ROOT))
        from julius.wake.train_head import Head
    except ImportError as e:
        return {"kept": False, "reason": f"training needs torch and onnx ({e.name}); clips are still being collected"}
    pos, neg = store.positives(), store.negatives()
    if len(pos) < 2 * MIN_VALIDATION and not force: return {"kept": False, "reason": f"only {len(pos)} clips so far"}
    store.dir.mkdir(parents=True, exist_ok=True)
    if lock.exists() and time.time() - lock.stat().st_mtime < 3 * 3600: return {"kept": False, "reason": "already training"}
    lock.write_text(str(os.getpid()))
    try:
        rng = np.random.default_rng(seed); torch.manual_seed(seed)
        n_val = max(MIN_VALIDATION, len(pos) // 4); n_val = min(n_val, len(pos) // 2)
        val_pos, train_pos = pos[-n_val:], pos[:-n_val]                       # the NEWEST clips judge the result
        nv = max(0, min(len(neg) // 4, len(neg) - 1)) if len(neg) >= 4 else 0
        val_neg, train_neg = (neg[-nv:] if nv else []), neg[:len(neg) - nv]
        feat = Model(wakeword_models=[str(starter)], inference_framework="onnx"); fname = list(feat.models)[0]
        def emb(clips): return [stream_windows(feat, fname, c) for c in clips]   # features exactly as the live detector computes them (clip-at-once embedding differs)
        def clip_scores(m, wins):
            with torch.no_grad(): return np.array([float(m(torch.from_numpy(w)).max()) if len(w) else 0.0 for w in wins])
        def windows(w): return w
        tp = [_read(p) for p in train_pos]
        aug = [_augment(x, rng) for x in tp for _ in range(24)] + [x for x in tp]
        tn = [_read(p) for p in train_neg]
        aug_neg = [_augment(x, rng) for x in tn for _ in range(6)] + tn
        noise = []
        for i in range(150):
            w = rng.standard_normal(4 * SR).astype(np.float32)
            if i % 3 == 1: f = np.fft.rfft(w); f /= np.sqrt(np.maximum(np.arange(len(f)), 1)); w = np.fft.irfft(f, 4 * SR).astype(np.float32)
            noise.append(w / np.abs(w).max() * rng.uniform(.02, .5))
        e_pos, e_neg, e_noise = emb(aug), emb(aug_neg) if aug_neg else [], emb(noise)
        model = Head(); model.load_state_dict(torch.load(starter_head, map_location="cpu")); opt = torch.optim.AdamW(model.parameters(), 3e-4, weight_decay=1e-3); bce = nn.BCELoss(reduction="none")
        data = [(e, 1.0, 1.0) for e in e_pos] + [(e, 0.0, 2.0) for e in e_neg] + [(e, 0.0, 1.0) for e in e_noise]
        order = np.random.default_rng(seed)
        for ep in range(epochs):
            model.train(); order.shuffle(data)
            for i in range(0, len(data), 32):
                batch = data[i:i + 32]; xs, ids, y, w = [], [], [], []
                for k, (e, label, weight) in enumerate(batch):
                    wi = windows(e); xs.append(torch.from_numpy(wi)); ids += [k] * len(wi); y.append(label); w.append(weight)
                s = model(torch.cat(xs)).squeeze(1); ids_t = torch.tensor(ids)
                clip = torch.stack([s[ids_t == k].max() for k in range(len(batch))])
                loss = (bce(clip, torch.tensor(y)) * torch.tensor(w)).mean(); opt.zero_grad(); loss.backward(); opt.step()
        model.eval()
        # the current model is the personal one if there is one, otherwise the starter; judged on the held-out recent clips only
        cur_head = Head()
        if store.model.exists() and (store.dir / "head.pt").exists(): cur_head.load_state_dict(torch.load(store.dir / "head.pt", map_location="cpu"))
        else: cur_head.load_state_dict(torch.load(starter_head, map_location="cpu"))
        cur_head.eval()
        vp, vn = emb([_read(p) for p in val_pos]), (emb([_read(p) for p in val_neg]) if val_neg else [])
        def judge(m):
            r = (clip_scores(m, vp) >= threshold).mean(); f = (clip_scores(m, vn) >= threshold).mean() if vn else 0.0
            return {"recall": float(r), "false_rate": float(f)}
        cur, cand = judge(cur_head), judge(model); kept, why = decide(cur, cand)
        result = {"kept": kept, "reason": why, "current": cur, "candidate": cand, "validation_positives": len(val_pos), "validation_negatives": len(val_neg), "trained_on": len(train_pos), "when": time.strftime("%Y-%m-%d %H:%M")}
        if kept:
            tmp = store.dir / "model.tmp.onnx"
            torch.onnx.export(model, torch.zeros(1, 16, 96), tmp, input_names=["input"], output_names=["output"], dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}}, opset_version=17, dynamo=False)
            torch.save(model.state_dict(), store.dir / "head.pt"); os.replace(tmp, store.model)
        store.save_state(positives_at_last_train=len(pos), last_result=result)
        log(json.dumps(result)); return result
    finally:
        try: lock.unlink()
        except OSError: pass


# ---------------- optional quick setup ----------------

def record_clip(seconds=2.5, device=None):
    import sounddevice as sd
    x = sd.rec(int(seconds * SR), samplerate=SR, channels=1, dtype="float32", device=device); sd.wait(); return x[:, 0]


def setup(store=None, count=5, record=record_clip, ask=input, say=print):
    """Optional: record up to `count` clips of you saying "Julius". Press Enter to record each; type s to skip the rest. A UI can call the same function with its own prompts."""
    store = store or Store(); done = 0
    say(f"Quick setup: say \"Julius\" (or \"Hey Julius\") naturally, up to {count} times. Enter = record, s = skip the rest. You can skip this entirely; Julius also learns from normal use.")
    for i in range(count):
        if ask(f"  [{i + 1}/{count}] Enter to record, s to skip: ").strip().lower().startswith("s"): break
        clip = record()
        if float(np.abs(clip).max()) < 0.01: say("  that was too quiet, skipping this one"); continue
        store.add_positive(np.concatenate([np.zeros(int(.3 * SR), np.float32), clip]), "setup"); done += 1
    say(f"Saved {done} clip(s). {store.status()['positives']} in total; training starts after {store.state().get('learn_after', 20)} new ones.")
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0]); ap.add_argument("cmd", choices=["setup", "status", "retrain", "forget"])
    ap.add_argument("--dir"); ap.add_argument("--force", action="store_true"); ap.add_argument("--yes", action="store_true"); a = ap.parse_args(argv)
    store = Store(a.dir)
    if a.cmd == "setup": setup(store)
    elif a.cmd == "status": print(json.dumps(store.status(), indent=1))
    elif a.cmd == "retrain": print(json.dumps(retrain(store, force=a.force), indent=1))
    else:
        if a.yes or input(f"Delete every collected wake clip and the personal model in {store.dir}? Type yes: ").strip().lower() == "yes": store.forget(); print("deleted")
        else: print("kept")


if __name__ == "__main__":
    main()
