"""Speaking: one sentence at a time through the voice, the speaker and the microphone gate."""

import json
import os
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import sounddevice as sd

from julius import log
from julius.audio.output import ready_output
from julius.audio.voice_identity import delivery_mode
from julius.brain.backend import close_backend, keep_brain_loaded
from julius.runtime.session import VOICE_SPEED, Session
from julius.runtime.text import clean_for_speech, split_long_sentence

CLONE_BACKENDS = ("qwen-clone", "qwen-mlx")


def print_tool(name: str, out: str) -> None:
    """Show a tool result on screen: long ones in full, short ones on one line."""
    if out.count("\n") > 3:
        log.console(f"  [{name}]", flush=True)
        for line in out.splitlines():
            log.console(f"    {line}", flush=True)
    else:
        log.console(f"  [-> {out[:300]}]", flush=True)


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


def _play_demo(s: Session, audio: Any, text: str) -> None:
    """Demo mode, no speaker: the voice is made as usual (all of it first: it is made slower than it plays), then "played" silently in real
    time so the orb's speaking FX follow the real audio (the level tap gets each 1/30 s as it would leave the speakers), and the utterance
    is saved for the video's soundtrack (JULIUS_DEMO_SPEECH_DIR: one WAV per utterance, and speech.jsonl with its wall-clock start, length
    and text)."""
    import numpy as np

    parts, rate = [], 0
    for samples, sample_rate in audio:
        if s.cancellation:
            s.cancellation.check()
        parts.append(np.asarray(samples, dtype=np.float32).reshape(-1))
        rate = rate or int(sample_rate)
    if not parts or not rate:
        return
    wav = np.concatenate(parts)
    _note_audio(s)
    t0, step = time.time(), max(1, rate // 30)
    for i in range(0, len(wav), step):
        if s.cancellation:
            s.cancellation.check()
        if s.ui_level_tap is not None:
            s.ui_level_tap.out(wav[i : i + step], rate)
        ahead = t0 + (i + step) / rate - time.time()
        if ahead > 0:
            time.sleep(ahead)
    out_dir = os.environ.get("JULIUS_DEMO_SPEECH_DIR", "")
    if out_dir:
        import soundfile as sf

        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{t0:.3f}.wav"
        sf.write(folder / name, wav, rate)
        with (folder / "speech.jsonl").open("a") as f:
            f.write(
                json.dumps(
                    {
                        "t": round(t0, 3),
                        "dur": round(len(wav) / rate, 3),
                        "text": text,
                        "file": name,
                    }
                )
                + "\n"
            )


def _play_chunks(s: Session, audio: Any) -> None:
    """Fallback: play each chunk to completion."""
    for samples, sample_rate in audio:
        _note_audio(s)
        if not s.no_devices:
            sd.play(samples, sample_rate)
            sd.wait()


def _play(s: Session, audio: Iterator[Any], text: str = "") -> None:
    if s.demo and s.no_devices:
        _play_demo(s, audio, text)
    elif s.tts.backend == "qwen-mlx" and not s.no_devices and s.speech_player is not None:
        _play_with_player(s, audio)
    elif s.tts.backend == "qwen-mlx" and not s.no_devices:
        _play_on_one_stream(s, audio)
    else:
        _play_chunks(s, audio)


def _release_brain_for_voice(s: Session) -> None:
    """A previous failed reasoning request must not leave its model beside voice."""
    if s.tts.backend in CLONE_BACKENDS and not keep_brain_loaded():
        close_backend()


def display_text(t: str) -> str:
    """What the transcript shows: his words as written ("settings.json", "3 PM", "$155"), not the spoken form the voice gets
    ("settings dot json", "three P M"). Only markdown marks are taken out."""
    t = re.sub(r"\*\*|__|`|^#+\s*", "", t, flags=re.M)
    t = re.sub(r"(?<!\w)\*(?=\S)|(?<=\S)\*(?!\w)", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _show(s: Session, display: str) -> None:
    if s.events_hub is not None and display:
        s.events_hub.say("julius", display, s.turn)


def _speak_piece(s: Session, text: str, show: bool = True) -> None:
    raw = text
    text = clean_for_speech(text)
    if not text:
        return
    if show:
        _show(s, display_text(raw))
    if s.text_only:
        return  # typed in the orb with ui.typed_reply = text: shown, not spoken
    if s.self_echo is not None:
        s.self_echo.note(text)
    _release_brain_for_voice(s)
    _play(s, s.tts.synthesize(text, speed=_speed_for(text)), text)


def speak_sentence(s: Session, text: str, display: str | None = None) -> None:
    """Speak one sentence, split into clauses when it is long so the voice does not slow down. The transcript gets the sentence once,
    as written (`display`, else the text itself without markdown)."""
    if clean_for_speech(text):
        _show(s, display_text(display if display is not None else text))
    for part in split_long_sentence(text):
        with s.microphone_gate.output():
            _speak_piece(s, part, show=False)


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
            sentences = [
                x.strip() for x in re.split(r"(?<=[.!?…])\s+", clean_for_speech(text)) if x.strip()
            ]
            written = [
                x.strip() for x in re.split(r"(?<=[.!?…])\s+", display_text(text)) if x.strip()
            ]
            same = len(written) == len(
                sentences
            )  # the transcript shows the written sentence when the two line up
            for i, sentence in enumerate(sentences):
                s.cancellation.check()
                shown = written[i] if same else None
                if shown is None or shown == sentence:
                    speak_sentence(s, sentence)
                else:
                    speak_sentence(s, sentence, display=shown)
    finally:
        s.tts.response_delivery_mode = previous
