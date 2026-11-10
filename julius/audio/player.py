"""Speech playback that survives a voice generating at about real-time speed.

The MLX voice streams ~0.3 s chunks and produces them at 0.85 to 0.99x real time, so the next chunk often lands with almost no slack.
This player (1) holds back playback until one extra chunk has arrived (prefill), (2) asks the device for a larger buffer, (3) keeps one
stream open between sentences (no start/stop pop per sentence) and ends each sentence with a short silent tail, (4) raises the prefill
after an underflow and relaxes it after clean sentences, and (5) records every underflow so live runs show them.
"""

import contextlib
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import Mock

import numpy as np
from numpy import ndarray

from julius.audio.duplex import _OutputAdapter


class SpeechPlayer:
    def __init__(
        self,
        sd: Mock | Any,
        prefill_chunks: int = 1,
        latency: float = 0.4,
        idle_seconds: int | float = 1.5,
        max_prefill: int = 4,
        tail_seconds: float = 0.02,
        clock: Callable = time.perf_counter,
        log: Callable = print,
        sleep: Callable = time.sleep,
        drain_margin: float = 0.05,
    ) -> None:
        self.sd, self.base_prefill, self.latency, self.idle_seconds, self.max_prefill = (
            sd,
            prefill_chunks,
            latency,
            idle_seconds,
            max_prefill,
        )
        self.tail_seconds, self.clock, self.log = tail_seconds, clock, log
        self.sleep, self.drain_margin = sleep, drain_margin
        self.prefill_chunks = prefill_chunks
        self.stream = None
        self.rate = None
        self.events = []  # underflows since the last pop_events()
        self.total_underflows = 0
        self._clean_run = 0
        self._lock = threading.RLock()
        self._timer = None

    # ---- stream lifetime ----
    def _open(self, rate: int) -> _OutputAdapter | Any | Mock:
        if self.stream is not None and self.rate != rate:
            self._close_stream()
        if self.stream is None:
            self.stream = self.sd.OutputStream(
                samplerate=rate, channels=1, dtype="float32", latency=self.latency
            )
            self.stream.start()
            self.rate = rate
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        return self.stream

    def _close_stream(self, abort: bool = False) -> None:
        stream, self.stream, self.rate = self.stream, None, None
        if stream is None:
            return
        try:
            (stream.abort if abort else stream.stop)()
        finally:
            stream.close()

    def _idle_close(self) -> None:
        with self._lock:
            self._timer = None
            self._close_stream()

    def abort(self) -> None:
        """Silence the speakers now, from any thread (the stop hotkey). Does not take the playback lock: play() may be blocked in write()."""
        stream = self.stream
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.abort()

    def close(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._close_stream()

    # ---- playback ----
    def _tap_audio(self, audio: ndarray, sample_rate: int) -> None:
        """Hand a copy of the audio to the session recorder; it may never disturb playback."""
        tap = getattr(self, "tap", None)
        if tap is not None:
            with contextlib.suppress(Exception):
                tap(audio, sample_rate)

    def play(
        self,
        chunks: Iterator[Any],
        cancel_check: Callable | None = None,
        on_start: Callable | None = None,
    ) -> dict[str, int]:
        """Play (float32 chunk, sample_rate) pairs from a generator. Returns {'chunks', 'underflows', 'prefill_chunks'}."""
        with self._lock:
            it = iter(chunks)
            try:
                first = next(it)
            except StopIteration:
                return {"chunks": 0, "underflows": 0, "prefill_chunks": self.prefill_chunks}
            rate = first[1]
            held = [first]
            while len(held) < 1 + self.prefill_chunks:  # hold playback until the lead is built
                try:
                    held.append(next(it))
                except StopIteration:
                    break
            stream = self._open(rate)
            if on_start is not None:
                on_start()  # first audio is about to be written
            started, sentence_underflows, written, seconds_written = self.clock(), 0, 0, 0.0
            try:

                def put(audio, sample_rate, index) -> None:
                    nonlocal sentence_underflows, written, seconds_written
                    if sample_rate != rate:
                        raise RuntimeError("Speech sample rate changed")
                    self._tap_audio(audio, sample_rate)
                    flagged = stream.write(
                        np.ascontiguousarray(audio, dtype=np.float32).reshape(-1, 1)
                    )
                    if (
                        flagged and index > 0
                    ):  # the first write of a sentence follows the natural pause between sentences
                        sentence_underflows += 1
                        event = {
                            "at_seconds": round(self.clock() - started, 2),
                            "chunk": index,
                            "prefill_chunks": self.prefill_chunks,
                        }
                        self.events.append(event)
                        self.log(
                            f"  [audio underflow at chunk {index}, {event['at_seconds']} s into the sentence]",
                            flush=True,
                        )
                    written += 1
                    seconds_written += len(audio) / sample_rate

                for index, (audio, sample_rate) in enumerate(held):
                    put(audio, sample_rate, index)
                ahead, lead, exhausted = (
                    [],
                    0,
                    False,
                )  # lead: chunks to keep queued ahead once a sentence has underflowed
                while True:
                    if cancel_check is not None:
                        cancel_check()
                    while not exhausted and len(ahead) < 1 + lead:
                        try:
                            ahead.append(next(it))
                        except StopIteration:
                            exhausted = True
                    if not ahead:
                        break
                    before = sentence_underflows
                    audio, sample_rate = ahead.pop(0)
                    put(audio, sample_rate, written)
                    if (
                        sentence_underflows > before
                    ):  # the voice is falling behind: fewer, longer gaps beat a stutter on every chunk
                        lead = min(self.max_prefill, lead + 1)
                stream.write(
                    np.zeros((int(self.tail_seconds * rate), 1), dtype=np.float32)
                )  # clean end: the stream stays open on silence
                seconds_written += self.tail_seconds
                # write() returns once audio is queued, not once it has been heard. Wait for the queue to play out so the microphone
                # gate does not reopen while Julius is still speaking (it would transcribe its own voice).
                remaining = (
                    started
                    + seconds_written
                    - self.clock()
                    + self.drain_margin
                    + (0.15 if sentence_underflows else 0.0)
                )
                if remaining > 0:
                    self.sleep(remaining)
            except BaseException:
                self._close_stream(
                    abort=True
                )  # cancelled or failed: stop now, reopen on the next sentence
                if cancel_check is not None:
                    cancel_check()  # an abort() from the hotkey surfaces as a write error; report it as the cancellation it is
                raise
            finally:
                close = getattr(it, "close", None)
                if callable(close):
                    close()
            self._adapt(sentence_underflows)
            self.total_underflows += sentence_underflows
            self._timer = threading.Timer(self.idle_seconds, self._idle_close)
            self._timer.daemon = True
            self._timer.start()
            return {
                "chunks": written,
                "underflows": sentence_underflows,
                "prefill_chunks": self.prefill_chunks,
            }

    def _adapt(self, underflows: int) -> None:
        """More prefill after an underflow (up to max_prefill), back toward the base after five clean sentences."""
        if underflows:
            self.prefill_chunks = min(self.max_prefill, self.prefill_chunks + 1)
            self._clean_run = 0
        else:
            self._clean_run += 1
            if self._clean_run >= 5 and self.prefill_chunks > self.base_prefill:
                self.prefill_chunks -= 1
                self._clean_run = 0

    def pop_events(self) -> list[Any | dict[str, float | int]]:
        events, self.events = self.events, []
        return events
