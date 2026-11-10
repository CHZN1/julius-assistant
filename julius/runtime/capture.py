"""Microphone capture: the input stream, talk-over detection and the room-noise calibration."""

import contextlib
import time
from contextlib import nullcontext
from typing import Any

import numpy as np
import sounddevice as sd
from numpy import ndarray

from julius import log
from julius.audio.duplex import replace_with_raw
from julius.runtime.controls import stop_now
from julius.runtime.session import CHUNK, SAMPLE_RATE, Session


def microphone_stream(s: Session) -> Any:
    """The context manager that keeps audio flowing into `s.chunks`."""
    if s.duplex is not None:
        return s.duplex.running()
    if s.no_devices:
        return nullcontext()
    return sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        blocksize=CHUNK,
        latency="high",
        callback=lambda indata, frames, when, status: callback(s, indata, frames, when, status),
    )


def talked_over(s: Session, duplex: bool = False) -> None:
    """the user started speaking while Julius was talking: cut the speech now and listen to him."""
    if s.cancellation.busy.is_set():
        print("  [you spoke over Julius: stopping]", flush=True)
        s.rec_event("talk_over")
        stop_now(s, "voice")
    else:
        print("  [you started speaking right after Julius finished: listening]", flush=True)
    s.microphone_gate.force_open()
    s.talkover_pending = (
        True  # the main loop must not clear what the user said while the turn was cancelled
    )
    if s.capture_clock is not None:
        s.capture_clock.previous = None  # the gate was shut: the first saved block looks like a gap
    if duplex:
        keep_duplex_audio(s)
        return
    s.chunks.clear()
    for chunk in list(s.recent_gated):
        s.chunks.append((chunk, time.perf_counter()))
    s.recent_gated.clear()


def keep_duplex_audio(s: Session) -> None:
    """The microphone was open the whole time: what he said is already queued (echo-cancelled);
    keep the last 4 s of it."""
    keep = list(s.chunks)[-int(4.0 * SAMPLE_RATE / CHUNK) :]
    s.chunks.clear()
    s.chunks.extend(keep)
    # Off (11-07): the recognizer's input comes from the echo-cancelled buffer only; the raw
    # microphone also carries Julius's own voice, which leaked into transcripts.
    if s.audio_cfg.get("raw_backfill"):
        replace_with_raw(s.chunks, s.duplex.ring, s.duplex_voice.onset_ago + 0.45)
        s.duplex.raw_blocks_left = 12
        s.use_raw_now = True


def duplex_block(s: Session, block: Any, raw: str, ref: Any, cleaned: Any) -> None:
    """One 1024-sample block from the duplex stream (audio thread)."""
    if s.rec is not None:
        s.rec.pair(raw, ref)
    s.current_raw = raw
    callback(s, np.asarray(block, dtype=np.float32).reshape(-1, 1), len(block), None, None)


def duplex_fallback(s: Session, why: str) -> None:
    """The canceller is not removing the echo: listen to the raw microphone through the gate again."""
    s.microphone_gate.bypass = False
    if s.duplex is not None:
        s.duplex.use_cleaned = False
    s.rec_event("duplex_fallback", reason=why)
    with contextlib.suppress(Exception):
        log.event("duplex_fallback", reason=why)


def _duplex_talkover(s: Session, samples: Any) -> None:
    """Echo-cancelled stream: his voice over Julius's is detected in the cleaned signal."""
    if s.duplex_monitor.fell_back or not s.cancellation.busy.is_set() or s.microphone_gate.quiet:
        return
    try:
        if s.duplex_voice.block(samples):
            talked_over(s, duplex=True)  # latches until his playback has ended: one stop per reply
    except Exception:
        pass


def _gated_talkover(s: Session, samples: ndarray) -> None:
    """Gate shut while Julius speaks: keep the audio, and listen for a voice over his."""
    gate = s.microphone_gate
    if s.talkover is None or gate.quiet:
        return
    if not (s.cancellation.busy.is_set() or gate.settling()):
        return
    s.recent_gated.append(samples.copy())
    try:
        if s.talkover.mic(samples):
            talked_over(s)
            s.talkover.triggered = False
    except Exception:
        pass


def callback(s: Session, indata: ndarray, frames: int, when: None, status: None) -> None:
    """The audio callback: queue accepted blocks, and watch for the user talking over Julius."""
    samples = indata[:, 0]
    if s.rec is not None and s.duplex is None:
        s.rec.mic(samples)
    if s.duplex is not None:
        _duplex_talkover(s, samples)
    if s.use_raw_now:
        s.use_raw_now = False
        indata = np.asarray(s.current_raw, dtype=np.float32).reshape(-1, 1)  # the triggering block
        samples = indata[:, 0]
    if s.microphone_gate.accepting():
        s.chunks.append((samples.copy(), time.perf_counter()))
    else:
        _gated_talkover(s, samples)


def calibrate(s: Session) -> None:
    """Listen to the room for two seconds and set the speech threshold from its noise floor."""
    print("calibrating room noise — stay quiet 2s...")
    with microphone_stream(s):
        time.sleep(2)
    heard = [audio for audio, _ in s.chunks]
    floor = float(np.abs(np.concatenate(heard)).mean()) if heard else 0.001
    # Low floor-relative trigger on a quiet mic, with a conservative upper cap.
    s.threshold = min(0.020, max(0.0045, floor * 2.2))
    s.chunks.clear()
    s.pre_roll.clear()
    print(f"noise floor {floor:.4f} -> threshold {s.threshold:.4f} (0.5s preroll)")
