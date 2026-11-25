"""Always-listening wake word for the main loop: [wake] engine = "cascade" in julius.toml (engine = "whisper" keeps the old transcript-regex wake).

Cascade: openWakeWord "Julius" detector (noise gated, low threshold) -> optional personal speaker verifier (~/.julius/wake_verifier.pkl; skipped when the file
is absent) -> one Whisper run on the whole utterance that checks the wake word and gives the request. See wake/stream.py and wake/verifier.py.

Every stage-1 fire is logged to <memory>/wake_log.jsonl with its detector score, verifier score and the Whisper decision, plus a `repeat` row when a fire follows
another within 5 s (a likely missed wake). The short audio clip is saved to <memory>/wake_clips/ (local, git-ignored) only for near-misses: a rejected fire that
is followed by another within 5 s, and accepted wakes whose verifier score was within 0.1 of its threshold."""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from julius import identity
from julius.paths import REPO_ROOT

FRAME = 1280
REPEAT_WINDOW = 5.0


class WakeEngine:
    def __init__(
        self,
        stream: Any,
        log_dir: Any,
        clock: Callable[[], float] = time.time,
        repeat_window: Any = REPEAT_WINDOW,
        speaker_margin: float = 0.1,
        learner: Any | None = None,
        reloader: Any | None = None,
    ) -> None:
        self.learner = learner
        self.reloader = reloader  # learner: wake/personal.Learner (passive clips); reloader: () -> new WakeStream when a better model appeared, else None
        self.stream = stream
        self.clock = clock
        self.repeat_window = repeat_window
        self.speaker_margin = speaker_margin
        self.log_dir = Path(log_dir)
        self.buffer = np.zeros(0, np.float32)
        self.previous = None

    def feed(self, chunk: Any) -> Any:
        """Take any amount of 16 kHz float audio; return the accepted wake events decided on it ({"request": text after the wake word, ...})."""
        self.buffer = np.concatenate([self.buffer, np.asarray(chunk, dtype=np.float32).reshape(-1)])
        accepted = []
        while len(self.buffer) >= FRAME:
            frame, self.buffer = self.buffer[:FRAME], self.buffer[FRAME:]
            for event in self.stream.push((np.clip(frame, -1, 1) * 32767).astype(np.int16)):
                self._log(event)
                if event["accepted"]:
                    accepted.append(event)
        return accepted

    def reset(self) -> None:
        if self.reloader is not None:
            try:
                fresh = self.reloader()
            except Exception:
                fresh = None
            if fresh is not None:
                self.stream = fresh
        if self.learner is not None:
            self.learner.flush()
        self.buffer = np.zeros(0, np.float32)
        self.stream.reset()

    def note_spoken_wake(self, text: str) -> None:
        """The main loop heard the wake word while already awake (Julius, again): worth knowing when it follows a recent rejected fire."""
        self._write({"event": "wake_while_awake", "heard": text, **self._repeat_info(self.clock())})

    def _repeat_info(self, now: Any) -> Any:
        gap = None if self.previous is None else now - self.previous["time"]
        return {
            "gap_s": None if gap is None else round(gap, 2),
            "repeat": gap is not None and gap <= self.repeat_window,
        }

    def _log(self, event: Any) -> None:
        if self.learner is not None:
            self.learner.observe(event)
        now = self.clock()
        info = self._repeat_info(now)
        row = {
            "event": "fire",
            "score": round(event["score"], 3),
            "speaker": None if event.get("speaker") is None else round(event["speaker"], 3),
            "accepted": event["accepted"],
            "reason": event.get("reason", ""),
            "heard": event["heard"],
            "whisper_s": round(event["verify_s"], 3),
            **info,
        }
        prev = self.previous
        if (
            info["repeat"]
            and prev is not None
            and not prev["accepted"]
            and prev.get("clip") is not None
        ):
            row["near_miss_clip"] = self._save(
                prev["clip"], "repeat_after_reject", prev["time"]
            )  # the earlier rejection may have been a real wake
        if (
            event["accepted"]
            and event.get("clip") is not None
            and event.get("speaker") is not None
            and self.stream.speaker is not None
        ) and event["speaker"] - self.stream.speaker_threshold < self.speaker_margin:
            row["near_miss_clip"] = self._save(event["clip"], "borderline_accept", now)
        self._write(row)
        self.previous = {"time": now, "accepted": event["accepted"], "clip": event.get("clip")}

    def _save(self, clip: Any, why: Any, when: Any) -> Any:
        import soundfile as sf

        folder = self.log_dir / "wake_clips"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (time.strftime("%Y%m%d_%H%M%S", time.localtime(when)) + f"_{why}.wav")
        sf.write(path, np.asarray(clip, dtype=np.float32), 16000, subtype="PCM_16")
        return str(path)

    def _write(self, row: Any) -> None:
        row = {"ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.clock())), **row}
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with (self.log_dir / "wake_log.jsonl").open("a") as fh:
            fh.write(json.dumps(row) + "\n")


def _starter_model(root: Path, ident: identity.Identity | None = None) -> Path:
    """The shipped starter model for the configured wake word (julius_starter.onnx by default). A renamed wake word has none until
    it is trained, and from_config then falls back to the Whisper wake, which already listens for the new word."""
    ident = ident or identity.current()
    return Path(root) / "julius" / "wake" / "models" / f"{ident.model_stem}_starter.onnx"


def missing_model_line(model: Path, ident: identity.Identity | None = None) -> str:
    ident = ident or identity.current()
    if ident.model_stem == "julius":
        return f"  [wake: {model.name} missing, using the Whisper wake]"
    return (
        f"  [wake: no trained model for {ident.wake_word!r} yet ({model.name}), using the Whisper wake. To train one: "
        "python -m julius.wake.build_dataset, then python -m julius.wake.train_head (see julius/identity.py)]"
    )


def choose_model(wake: Any, root: Any | None = None, personal_dir: Any | None = None) -> Any:
    """(path, threshold, learns). An explicit [wake] model wins (your own private detector: threshold_low, no passive learning). Otherwise your personal model from
    passive learning if there is one, else the shipped starter model; both use starter_threshold, very low because the Whisper stage keeps false wakes at zero."""
    root = Path(root or REPO_ROOT)
    if str(wake.get("model") or "").strip():
        m = Path(str(wake["model"])).expanduser()
        m = m if m.is_absolute() else root / m
        return m, float(wake.get("threshold_low", 0.05)), False
    import importlib

    personal = importlib.import_module("julius.wake.personal").Store(personal_dir).model
    thr = float(wake.get("starter_threshold", 0.0005))
    return (
        (personal if personal.exists() else _starter_model(root)),
        thr,
        bool(wake.get("learn", True)),
    )


def from_config(
    verify: Any,
    log_dir: Any,
    config: dict[str, Any] | None = None,
    say: Callable[[str], Any] = print,
) -> Any:
    """The engine described by [wake] in julius.toml, or None for engine = "whisper" or when the detector model is missing (falls back, saying why)."""
    import julius.config as cfg

    wake = (config or cfg.load_config())["wake"]
    if wake.get("engine", "whisper") != "cascade":
        return None
    model, threshold, learns = choose_model(wake)
    if not model.exists():
        say(missing_model_line(model))
        return None
    try:
        from julius.wake.stream import WakeStream

        speaker = None
        vpath = Path(str(wake.get("verifier", "~/.julius/wake_verifier.pkl"))).expanduser()
        if vpath.exists():
            import pickle

            with vpath.open("rb") as fh:
                speaker = pickle.load(fh)  # your own file, written by wake/verifier.py

        def make(path: str | Path, thr: Any) -> Any:
            return WakeStream(
                path,
                thr,
                verify=verify,
                speaker=speaker,
                speaker_threshold=float(wake.get("speaker_threshold", 0.25)),
                level_floor=float(wake.get("level_floor", 0.0015)),
            )

        stream = make(model, threshold)
        learner = reloader = None
        if learns:
            from julius.wake.personal import Learner, Store

            learner = Learner(Store(), learn_after=int(wake.get("learn_after", 20)))
            loaded = [model, model.stat().st_mtime]

            def reloader() -> Any:
                new, thr, _ = choose_model(wake)
                if new.exists() and (new != loaded[0] or new.stat().st_mtime != loaded[1]):
                    fresh = make(new, thr)
                    loaded[:] = [new, new.stat().st_mtime]
                    return fresh

        which = "your personal model" if model.name == "model.onnx" else model.name
    except Exception as exc:
        say(f"  [wake: cascade unavailable ({type(exc).__name__}: {exc}), using the Whisper wake]")
        return None
    say(
        f"  [wake: cascade detector ({which}){' + speaker verifier' if speaker is not None else ''} + Whisper check{', learning from use' if learner else ''}]"
    )
    return WakeEngine(stream, log_dir, learner=learner, reloader=reloader)
