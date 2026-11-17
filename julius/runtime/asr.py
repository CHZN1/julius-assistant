"""Speech recognition: Whisper transcription, the wake-word second stage, and utterance saving."""

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

import mlx_whisper
import numpy as np
import soundfile as sf
from numpy import ndarray

import julius.memory.sessions as jm
from julius import log
from julius.audio.asr_resources import recognition_resources
from julius.audio.listening import has_voice_activity, has_wake
from julius.audio.speech_text import fix_transcript
from julius.brain.backend import close_backend, keep_brain_loaded
from julius.runtime.session import SAMPLE_RATE, Session
from julius.runtime.speech import CLONE_BACKENDS
from julius.wake import engine

NO_SPEECH_LIMIT = 0.6


def save_utterance(s: Session, audio: Any) -> None:
    """JULIUS_SAVE_AUDIO_DIR: keep each captured utterance (16 kHz mono) for offline checks."""
    folder = Path(os.environ["JULIUS_SAVE_AUDIO_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    s.saved_utterances += 1
    sf.write(folder / f"utt_{s.saved_utterances:03d}.wav", audio.astype("float32"), SAMPLE_RATE)


def _free_memory_for_recognition(s: Session) -> None:
    """Heavy voices and the local brain give way to the recognizer."""
    backend = s.tts.backend
    if backend == "qwen-clone" or (
        backend == "qwen-mlx" and os.environ.get("JULIUS_MLX_ASR_KEEP_VOICE") != "1"
    ):
        s.tts.suspend()
    if (
        backend in CLONE_BACKENDS
        and os.environ.get("JULIUS_ASR_RELEASE_LLM", "1") == "1"
        and not keep_brain_loaded()
    ):
        close_backend()


_SPRITE_NAMES: dict = {"t": 0.0, "names": []}


def request_hint(base: str | None) -> str | None:
    """The request vocabulary hint plus the art words and the names of saved sprites ("Sunday Roy"), refreshed once a minute."""
    from julius.runtime import heard

    if not base:  # [asr] vocabulary_hint = false (or no hint at all): leave it off
        return None
    now = time.monotonic()
    if now - _SPRITE_NAMES["t"] > 60:
        _SPRITE_NAMES["t"] = now
        try:
            from julius.art import tool

            _SPRITE_NAMES["names"] = [
                n for n, _t, _p in tool.saved_sprites()[:10] if not re.match(r"^(?:\d|Sprite )", n)
            ]
        except Exception:
            _SPRITE_NAMES["names"] = []
    extra = ", ".join(dict.fromkeys(heard.ART_VOCABULARY + _SPRITE_NAMES["names"]))
    return base.rstrip(". ") + ", " + extra + "."


def _whisper(s: Session, audio: ndarray, wake: bool) -> dict:
    """One mlx-whisper run with the wake or the request vocabulary hint."""
    resident_default = "0" if s.tts.backend in CLONE_BACKENDS else "1"
    language = os.environ.get("JULIUS_ASR_LANGUAGE", "en")
    hint = s.wake_hint if wake else request_hint(s.asr_hint)
    with recognition_resources(os.environ.get("JULIUS_ASR_RESIDENT", resident_default) == "0"):
        return mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=s.asr_model,
            condition_on_previous_text=False,
            language=None if language in ("", "auto") else language,
            no_speech_threshold=NO_SPEECH_LIMIT,
            **({"initial_prompt": hint} if hint else {}),
        )


def transcribe(s: Session, audio: ndarray | None, wake: bool = False) -> tuple[str, str, float]:
    """Text, language and no-speech probability for one utterance."""
    started = time.perf_counter()
    # A wake clip already passed the detector and its noise gate; quiet or distant speech sits
    # below the normal turn threshold, so the pre-check is relaxed for it.
    floor = min(s.threshold, 0.0015) if wake else s.threshold
    if not has_voice_activity(audio, SAMPLE_RATE, floor):
        s.last_asr_seconds = time.perf_counter() - started
        s.last_asr_confidence = {"rejected": "insufficient_audio_activity"}
        return "", "?", 1.0
    _free_memory_for_recognition(s)
    result = _whisper(s, audio, wake)
    segments = result.get("segments", [])
    no_speech = max((seg.get("no_speech_prob", 0) for seg in segments), default=0)
    s.last_asr_seconds = time.perf_counter() - started
    logprobs = [seg.get("avg_logprob") for seg in segments]
    s.last_asr_confidence = {
        "no_speech_probability": no_speech,
        "segment_log_probabilities": logprobs,
        "segment_temperatures": [seg.get("temperature") for seg in segments],
    }
    text = fix_transcript(result["text"].strip())
    s.rec_event(
        "asr",
        text=text,
        no_speech=round(float(no_speech), 3),
        logprob=logprobs,
        seconds=round(s.last_asr_seconds, 3),
        wake_pass=bool(wake),
    )
    return text, result.get("language", "?"), no_speech


def transcribe_input(s: Session, audio: list[int], wake: bool = False) -> tuple[str, str, float]:
    """A failed recognition turn must not stop listening or invent a request."""
    try:
        result = transcribe(s, audio, wake)
        if os.environ.get("JULIUS_DEBUG_ASR") == "1":
            text, lang, no_speech = result
            log.console(
                f"  [heard {text!r} lang={lang} no_speech={no_speech:.2f} wake_word={has_wake(text)}]",
                flush=True,
            )
        return result
    except Exception as exc:
        s.last_asr_confidence = {
            "rejected": "recognition_failure",
            "error_type": type(exc).__name__,
        }
        log.console(
            f"  [recognition failed: {type(exc).__name__}; input discarded, listening continues]",
            flush=True,
        )
        return "", "?", 1.0


def wake_verify(s: Session, clip: Any) -> tuple[bool, str, float]:
    """Cascade stage 2: one Whisper run on the whole utterance; the wake regex decides and the
    transcript becomes the request."""
    started = time.perf_counter()
    text, _lang, no_speech = transcribe_input(s, clip, wake=True)
    if text and s.self_echo.is_echo(text):
        s.rec_event("drop", reason="wake_echo", text=text)
        log.console("  [wake ignored: that was Julius's own voice]", flush=True)
        return False, text, time.perf_counter() - started
    heard = bool(text) and no_speech <= NO_SPEECH_LIMIT and has_wake(text)
    return heard, text, time.perf_counter() - started


def setup_wake(s: Session) -> None:
    """The wake-word cascade (None when running without devices), and an early Whisper load."""
    if s.no_devices:
        return
    s.wake_engine = engine.from_config(lambda clip: wake_verify(s, clip), jm.MEM)
    if os.environ.get("JULIUS_ASR_RESIDENT") == "1":
        # Load Whisper now so the first wake of the day is as fast as the rest.
        threading.Thread(
            target=lambda: mlx_whisper.transcribe(
                np.zeros(SAMPLE_RATE, np.float32), path_or_hf_repo=s.asr_model, language="en"
            ),
            daemon=True,
        ).start()
