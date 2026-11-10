"""Session recorder for the multi-turn voice eval (Phase F2). OFF unless JULIUS_SESSION_RECORD_DIR is set.

Writes, into one folder per conversation:
  mic.wav       everything the microphone heard, continuously (16 kHz mono), INCLUDING while Julius was speaking, so echo can be replayed
  out.wav       what Julius played, on the same timeline (silence elsewhere), the reference signal for echo cancellation
  events.jsonl  timestamped events: session start, each ASR result (text, no-speech probability, log-probability), drops, turns, replies, timing
These contain the user's voice and conversation text. They live under voice_data/ which is git-ignored and excluded from any release;
`python evals/voice_record.py delete-all` removes every recording.

Time base: all event times are unix seconds; sample N of mic.wav is at t0 + N / rate."""

import json
import os
import threading
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000


def _resample(audio, src, dst=RATE):
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if src == dst or len(audio) == 0:
        return audio
    n = int(round(len(audio) * dst / src))
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(
        np.float32
    )


class SessionRecorder:
    def __init__(self, folder, rate=RATE, clock=time.time, meta=None):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rate = rate
        self.clock = clock
        self.t0 = clock()
        self._lock = threading.Lock()
        self._mic_samples = 0
        self._out = []
        self._cursor = 0.0
        self._closed = False
        self._mic = open(self.dir / "mic.raw", "wb")
        self._events = open(self.dir / "events.jsonl", "a", buffering=1)
        self.event("session_start", rate=rate, t0=self.t0, **(meta or {}))

    # ---- inputs ----
    def mic(self, chunk):
        """Called from the audio callback with each float32 microphone block. Never raises: recording must not break the voice loop."""
        if self._closed:
            return
        try:
            pcm = (np.clip(np.asarray(chunk, dtype=np.float32).reshape(-1), -1, 1) * 32767).astype(
                np.int16
            )
            with self._lock:
                self._mic.write(pcm.tobytes())
                self._mic_samples += len(pcm)
        except Exception:
            pass

    def pair(self, raw, ref):
        """Duplex audio: the microphone as captured and what the speakers played in the same callback. The reference is placed at the microphone's own sample position, so they are exactly aligned."""
        if self._closed:
            return
        if self._mic_samples == 0:
            self.event(
                "mic_start"
            )  # the exact moment sample 0 of mic.wav was captured, on the event clock (the replay needs no guessing)
        try:
            pcm = (np.clip(np.asarray(raw, dtype=np.float32).reshape(-1), -1, 1) * 32767).astype(
                np.int16
            )
            a = np.asarray(ref, dtype=np.float32).reshape(-1)
            with self._lock:
                if np.abs(a).max(initial=0) > 1e-4:
                    self._out.append((self._mic_samples, a.copy()))
                    self._cursor = max(self._cursor, self._mic_samples + len(a))
                self._mic.write(pcm.tobytes())
                self._mic_samples += len(pcm)
        except Exception:
            pass

    def out(self, audio, sample_rate):
        """Called just before each playback chunk is written to the output device. Chunks play back to back, so they are laid end to end from the first one's time."""
        if self._closed:
            return
        try:
            a = _resample(audio, sample_rate, self.rate)
            now = (self.clock() - self.t0) * self.rate
            with self._lock:
                start = max(now, self._cursor)
                self._out.append((int(start), a))
                self._cursor = start + len(a)
        except Exception:
            pass

    def event(self, kind, **fields):
        try:
            with self._lock:
                self._events.write(
                    json.dumps({"t": round(self.clock(), 3), "kind": kind, **fields}, default=str)
                    + "\n"
                )
        except Exception:
            pass

    # ---- finish ----
    def close(self):
        if self._closed:
            return
        with self._lock:
            self._closed = True
            self._mic.close()
            n = self._mic_samples
            length = max(n, int(self._cursor))
            track = np.zeros(length, dtype=np.int16)
            for start, a in self._out:
                track[start : start + len(a)] = (np.clip(a, -1, 1) * 32767).astype(np.int16)[
                    : max(0, length - start)
                ]
        self.event(
            "session_end",
            mic_seconds=round(n / self.rate, 2),
            out_seconds=round(self._cursor / self.rate, 2),
        )
        self._events.close()
        finalize(self.dir, self.rate, out_track=track, length=length)


def finalize(folder, rate=RATE, out_track=None, length=None):
    """mic.raw -> mic.wav (padded to `length`), out track -> out.wav. Also usable on a folder left by a crash (no out track then)."""
    folder = Path(folder)
    raw = folder / "mic.raw"
    if not raw.exists():
        return
    pcm = np.frombuffer(raw.read_bytes(), dtype=np.int16)
    if length and len(pcm) < length:
        pcm = np.concatenate([pcm, np.zeros(length - len(pcm), dtype=np.int16)])
    for name, data in (("mic.wav", pcm), ("out.wav", out_track)):
        if data is None:
            continue
        with wave.open(str(folder / name), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(np.asarray(data, dtype=np.int16).tobytes())
    raw.unlink()


def from_env(meta=None):
    folder = os.environ.get("JULIUS_SESSION_RECORD_DIR")
    return SessionRecorder(folder, meta=meta) if folder else None


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        data = (
            np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32767
        )
    return data, rate


def read_events(folder):
    out = []
    p = Path(folder) / "events.jsonl"
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out
