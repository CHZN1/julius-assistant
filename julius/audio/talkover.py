"""Hears the user speaking over Julius so "stop" works by voice.

While Julius talks the microphone is gated (it would transcribe his own voice). This watches the gated audio instead: his echo in the microphone is roughly
a fixed fraction of what is being played, so sound well above that fraction, for long enough, is somebody talking. It only decides "someone is talking over me";
what to do then (stop speaking and listen) lives in julius.runtime.capture.

update() takes the microphone level and the recent playback level for a short window and returns True once, when the excess has lasted `sustain` seconds."""

import collections
import time
from collections.abc import Callable

import numpy as np
from numpy import ndarray


class TalkOver:
    def __init__(
        self,
        floor: float = 0.010,
        k: float = 2.2,
        sustain: float = 0.30,
        ratio: float = 0.06,
        out_memory: float = 2.0,
        clock: Callable = time.monotonic,
    ) -> None:
        self.floor, self.k, self.sustain, self.ratio, self.out_memory, self.clock = (
            floor,
            k,
            sustain,
            ratio,
            out_memory,
            clock,
        )
        self._out = collections.deque()  # (time, rms) of audio handed to the speakers
        self._run = 0.0
        self.triggered = False

    def out(self, audio, sample_rate=16000) -> None:
        """Playback tap: called as each chunk is written to the output device."""
        a = np.asarray(audio, dtype=np.float32)
        if a.size:
            self._out.append((self.clock(), float(np.sqrt(np.mean(a * a)))))

    def recent_out(self, now: None = None) -> float:
        now = self.clock() if now is None else now
        while self._out and now - self._out[0][0] > self.out_memory:
            self._out.popleft()
        return max((r for _, r in self._out), default=0.0)

    def update(self, mic_rms: float, out_rms: float, dt: float) -> bool:
        """One window. Returns True the moment talk-over is detected."""
        limit = max(self.floor, self.k * self.ratio * out_rms)
        if mic_rms > limit:
            self._run += dt
            if self._run >= self.sustain and not self.triggered:
                self.triggered = True
                return True
        else:
            self._run = 0.0
            if out_rms > 0.03 and mic_rms > 1e-5:
                self.ratio = min(
                    0.10, max(0.02, 0.97 * self.ratio + 0.03 * (mic_rms / out_rms))
                )  # learn this room's echo level from loud playback only
        return False

    def mic(self, chunk: ndarray, now: None = None, window: float = 0.1) -> bool:
        """Microphone block while Julius is speaking. Blocks are pooled into ~0.1 s windows before judging."""
        c = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if not c.size:
            return False
        self._acc = getattr(self, "_acc", 0.0) + float(np.sum(c * c))
        self._n = getattr(self, "_n", 0) + len(c)
        if self._n < window * 16000:
            return False
        rms = (self._acc / self._n) ** 0.5
        dt = self._n / 16000
        self._acc = 0.0
        self._n = 0
        return self.update(rms, self.recent_out(now), dt)

    def reset(self) -> None:
        self._run = 0.0
        self.triggered = False
        self._out.clear()
        self._acc = 0.0
        self._n = 0
