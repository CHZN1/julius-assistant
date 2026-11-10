"""Speaking: one sentence at a time through the voice, the speaker and the microphone gate."""

import re
import time
from collections.abc import Iterator
from typing import Any

import sounddevice as sd

from julius.audio.output import ready_output
from julius.audio.voice_identity import delivery_mode
from julius.brain.backend import close_backend, keep_brain_loaded
from julius.runtime.session import VOICE_SPEED, Session
from julius.runtime.text import clean_for_speech, split_long_sentence

CLONE_BACKENDS = ("qwen-clone", "qwen-mlx")


def print_tool(name: str, out: str) -> None:
    """Show a tool result on screen: long ones in full, short ones on one line."""
    if out.count("\n") > 3:
        print(f"  [{name}]", flush=True)
        for line in out.splitlines():
            print(f"    {line}", flush=True)
    else:
        print(f"  [-> {out[:300]}]", flush=True)


def _speed_for(text: str) -> float:
    """Questions slow down a little, exclamations and very short lines speed up."""
    if text.endswith("?"):
        return VOICE_SPEED * 0.97
    if text.endswith("!"):
        return VOICE_SPEED * 1.06
    if len(text) < 25:
        return VOICE_SPEED * 1.04
    return VOICE_SPEED


def _note_audio(s: Session) -> None:
    """Remember when the first audio of this response started playing."""
    if s.response_started is not None and s.first_audio_at is None:
        s.first_audio_at = time.perf_counter()


def _play_with_player(s: Session, audio: Iterator[Any]) -> None:
    """The persistent output stream used by the streamed MLX voice."""
    try:
        s.speech_player.play(
            audio,
            cancel_check=s.cancellation.check if s.cancellation else None,
            on_start=lambda: _note_audio(s),
        )
    finally:
        audio.close()


def _play_on_one_stream(s: Session, audio: Iterator[Any]) -> None:
    """A single output stream carries all chunks without repeated device starts."""
    try:
        first, rate = next(audio)
        with ready_output(
            lambda: sd.OutputStream(samplerate=rate, channels=1, dtype="float32"),
            sd.PortAudioError,
        ) as output:
            _note_audio(s)
            output.write(first.reshape(-1, 1))
            for chunk, sample_rate in audio:
                if s.cancellation:
                    s.cancellation.check()
                if sample_rate != rate:
                    raise RuntimeError("Speech sample rate changed")
                output.write(chunk.reshape(-1, 1))
    finally:
        audio.close()


def _play_chunks(s: Session, audio: Any) -> None:
    """Fallback: play each chunk to completion."""
    for samples, sample_rate in audio:
        _note_audio(s)
        if not s.no_devices:
            sd.play(samples, sample_rate)
            sd.wait()


def _play(s: Session, audio: Iterator[Any]) -> None:
    if s.tts.backend == "qwen-mlx" and not s.no_devices and s.speech_player is not None:
        _play_with_player(s, audio)
    elif s.tts.backend == "qwen-mlx" and not s.no_devices:
        _play_on_one_stream(s, audio)
    else:
        _play_chunks(s, audio)


def _release_brain_for_voice(s: Session) -> None:
    """A previous failed reasoning request must not leave its model beside voice."""
    if s.tts.backend in CLONE_BACKENDS and not keep_brain_loaded():
        close_backend()


def _speak_piece(s: Session, text: str) -> None:
    text = clean_for_speech(text)
    if not text:
        return
    if s.events_hub is not None:
        s.events_hub.say("julius", text, s.turn)
    if s.text_only:
        return  # typed in the orb with ui.typed_reply = text: shown, not spoken
    if s.self_echo is not None:
        s.self_echo.note(text)
    _release_brain_for_voice(s)
    _play(s, s.tts.synthesize(text, speed=_speed_for(text)))


def speak_sentence(s: Session, text: str) -> None:
    """Speak one sentence, split into clauses when it is long so the voice does not slow down."""
    parts = split_long_sentence(text)
    if len(parts) > 1:
        for part in parts:
            speak_sentence(s, part)
        return
    with s.microphone_gate.output():
        _speak_piece(s, text)


def speak(s: Session, text: str) -> None:
    """Speak a whole reply, sentence by sentence, stopping at once when the turn is cancelled."""
    previous = getattr(s.tts, "response_delivery_mode", None)
    s.tts.response_delivery_mode = delivery_mode(text)
    try:
        with s.cancellation.activity():
            if s.tts.backend == "qwen-mlx" and len(clean_for_speech(text)) <= 4096:
                s.cancellation.check()
                speak_sentence(s, text)
                return
            sentences = re.split(r"(?<=[.!?…])\s+", clean_for_speech(text))
            for sentence in (x.strip() for x in sentences if x.strip()):
                s.cancellation.check()
                speak_sentence(s, sentence)
    finally:
        s.tts.response_delivery_mode = previous
