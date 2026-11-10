"""The listening loop: housekeeping, typed input, wake word, utterance capture and hand-off to a turn."""

import contextlib
import os
import re
import time
from typing import Any

import numpy as np

import julius.memory.sessions as jm
from julius import config, log
from julius.agents import code_agent
from julius.audio.cancellation import TurnCancelled
from julius.audio.listening import (
    dangling,
    drop_stop_lead,
    has_wake,
    listening_control,
    stop_only,
    strip_leading_wake,
    strip_wake,
)
from julius.brain.backend import close_backend, keep_brain_loaded
from julius.runtime.asr import NO_SPEECH_LIMIT, save_utterance, transcribe_input
from julius.runtime.brain_turn import ask_streaming
from julius.runtime.capture import microphone_stream
from julius.runtime.controls import enter_quiet, leave_quiet, sleep_line, wants_quiet
from julius.runtime.handlers import announce_reminder, release_idle_models
from julius.runtime.session import (
    CHUNK,
    HOLD_SECONDS,
    MIN_SPEECH,
    SAMPLE_RATE,
    SESSION_SECONDS,
    Session,
)
from julius.runtime.speech import CLONE_BACKENDS, speak, speak_sentence
from julius.runtime.turn import process

MAX_UTTERANCE_CHUNKS = int(30 * SAMPLE_RATE / CHUNK)
CHECK_IN = "(no spoken input — check in on the user)"


def log_ui_pickup(entry: Any, remaining: int) -> None:
    """Log the moment the main loop takes a line typed in the orb off the queue (how long it waited)."""
    queued_at = getattr(entry, "queued_at", None)
    if getattr(entry, "from_ui", False) and queued_at is not None:
        waited = round(time.monotonic() - queued_at, 3)
        log.event("ui_typed", stage="picked_up", waited=waited, queue=remaining)


class Listener:
    """Reads microphone blocks and typed lines, and runs a turn when a request is complete."""

    def __init__(self, s: Session) -> None:
        self.s = s
        self.buffer: list = []
        self.silence_started: float | None = None
        self.speech_started: float | None = None
        self.voiced_run = 0
        self.longest_voiced_run = 0
        self.utterance_awake = False
        self.wake_prev_awake = True
        self.next_reminder_poll = 0.0

    # --- small helpers -------------------------------------------------------------------------
    def stay_awake(self) -> None:
        self.s.awake_until = time.time() + SESSION_SECONDS

    def finish_exchange(self) -> None:
        """Drop queued audio (it is Julius's own voice) and stay awake."""
        self.s.chunks.clear()
        self.stay_awake()

    def say(self, text: str) -> None:
        """Speak, treating a user stop as normal."""
        try:
            speak(self.s, text)
        except TurnCancelled:
            log.console("  [response stopped]", flush=True)

    def apply_control(self, text: str, control: str, record: bool = True) -> None:
        """'go to sleep' / 'wake up' (spoken or typed): change state, answer, free models on sleep."""
        s = self.s
        sleeping = control == "sleep"
        s.awake_until = 0.0 if sleeping else time.time() + SESSION_SECONDS
        log.console(f"  [{'asleep' if sleeping else 'awake'}]", flush=True)
        line = sleep_line(s, text) if sleeping else "I am here."
        if record:
            s.rec_event("reply", text=line, control=control)
        self.say(line)
        if sleeping:
            release_idle_models(s)
            if wants_quiet(s, text):
                enter_quiet(s)

    def say_yeah(self) -> None:
        """A bare "Julius": answer "Yeah?" and stay awake."""
        s = self.s
        self.stay_awake()
        s.rec_event("reply", text="Yeah?", control="wake")
        self.say("Yeah?")

    # --- housekeeping before each block --------------------------------------------------------
    def expire_wake(self) -> None:
        s = self.s
        if s.awake_until and time.time() >= s.awake_until and not s.recording:
            s.awake_until = 0.0
            log.console("  [asleep]\n", flush=True)
            release_idle_models(s)

    def poll_reminders(self) -> None:
        """Once a second, speak reminders that came due."""
        s = self.s
        if s.recording or time.monotonic() < self.next_reminder_poll:
            return
        self.next_reminder_poll = time.monotonic() + 1
        if not s.utility_session.planner.path.exists():
            return
        try:
            if s.utility_session.deliver_due(lambda message: announce_reminder(s, message)):
                s.chunks.clear()
        except Exception as exc:
            log.console(f"  [reminder delivery failed: {type(exc).__name__}: {exc}]", flush=True)
            self.next_reminder_poll = time.monotonic() + 30

    def answer_held(self) -> bool:
        """Nothing more came after a sentence he paused in: answer what he said."""
        s = self.s
        held = s.held
        if not (
            held and not s.recording and not s.chunks and time.time() - held["t"] > HOLD_SECONDS
        ):
            return False
        s.held = None
        process(s, held["text"], "en")
        self.finish_exchange()
        return True

    def vision_check_in(self) -> None:
        """When the camera sees him look a certain way for a while, check in once."""
        s = self.s
        emotion = s.eyes.check_nudge()
        if not emotion:
            return
        description = s.eyes.describe(s.eyes.snapshot())
        if s.tts.backend in CLONE_BACKENDS:
            segments: list[str] = []  # collected silently; the whole reply is spoken below
            reply = ask_streaming(
                s,
                CHECK_IN,
                description,
                nudge=emotion,
                speak_fn=segments.append,
                before_inference=s.tts.suspend,
            )
            if not keep_brain_loaded():
                close_backend()
            speak(s, reply)
        else:
            reply = ask_streaming(
                s, CHECK_IN, description, nudge=emotion, speak_fn=lambda t: speak_sentence(s, t)
            )
        s.chunks.clear()
        s.history.append({"role": "assistant", "content": reply})
        jm.log_turn("(vision check-in)", reply, {"emotion": emotion})
        self.stay_awake()

    def announce_noted(self) -> bool:
        """One short "Noted." per turn, spoken when he is not talking."""
        s = self.s
        if not (s.noted_pending and not s.recording and not s.chunks):
            return False
        age = time.time() - s.noted_pending
        s.noted_pending = None
        if age >= 10:
            return False
        with contextlib.suppress(TurnCancelled):
            speak(s, "Noted.")
        s.chunks.clear()
        return True

    def announce_code_result(self) -> bool:
        """A coding task finished while he was not talking: say how it went."""
        s = self.s
        if not (s.code_done and not s.recording and not s.chunks):
            return False
        said = code_agent.spoken_result(s.code_done.popleft())
        log.console(f"  JULIUS: {said}", flush=True)
        try:
            speak(s, said)
        except TurnCancelled:
            pass
        except Exception as exc:
            log.console(f"  [speech unavailable: {exc}; result shown as text]", flush=True)
        s.chunks.clear()
        return True

    # --- typed input ---------------------------------------------------------------------------
    def typed_reply_is_text(self) -> bool:
        return (config.load_config().get("ui") or {}).get("typed_reply") == "text"

    def announce_drop(self, entry: Any) -> None:
        """Files dropped on the orb: say what he got and ask; this is not a request."""
        s = self.s
        s.text_only = self.typed_reply_is_text()
        try:
            speak(s, str(entry))
        except TurnCancelled:
            log.console("  [response stopped]", flush=True)
        finally:
            s.text_only = False
        self.finish_exchange()

    def run_scripted_audio(self, entry: str) -> None:
        """Validation scripts can feed an audio file through recognition."""
        import soundfile as sf
        from scipy.signal import resample_poly

        audio, rate = sf.read(entry[7:], dtype="float32")
        text, lang, no_speech = transcribe_input(self.s, resample_poly(audio, SAMPLE_RATE, rate))
        if text and no_speech <= NO_SPEECH_LIMIT:
            process(self.s, text, lang)

    def run_typed_request(self, entry: Any) -> None:
        s = self.s
        s.text_only = bool(getattr(entry, "from_ui", False)) and self.typed_reply_is_text()
        started = time.monotonic()
        try:
            process(s, entry, "typed")
        except Exception as exc:
            if getattr(entry, "from_ui", False):
                log.event("ui_typed", stage="failed", error=type(exc).__name__)
            raise
        else:
            if getattr(entry, "from_ui", False):
                log.event(
                    "ui_typed",
                    stage="answered",
                    seconds=round(time.monotonic() - started, 2),
                    spoken=not s.text_only,
                )
        finally:
            s.text_only = False

    def handle_typed(self) -> None:
        """One queued line: `/quit`, a file-drop notice, a sleep/wake command, or a request."""
        s = self.s
        if s.typed[0] == "/quit":
            s.typed.popleft()
            raise KeyboardInterrupt
        entry = s.typed.popleft()
        log_ui_pickup(entry, len(s.typed))
        if getattr(entry, "drop_notice", False):
            self.announce_drop(entry)
            return
        control = listening_control(entry)
        if control:
            self.apply_control(entry, control, record=False)
            if control == "wake":
                leave_quiet(s, "typed")
            s.chunks.clear()
            return
        if s.scripted and entry.startswith("/audio "):
            self.run_scripted_audio(entry)
        else:
            self.run_typed_request(entry)
        self.finish_exchange()

    # --- the wake word -------------------------------------------------------------------------
    def after_wake(self, event: dict) -> None:
        """An accepted cascade wake: the request is the transcript after the wake word (same
        handling as a transcript that starts with 'Julius')."""
        s = self.s
        text = event["request"].strip()
        log.console("  [awake]", flush=True)
        control = listening_control(text) if text else None
        if control:
            self.apply_control(text, control)
        elif len(text.split()) < 2:
            self.say_yeah()
        else:
            process(s, text, "en")
            self.stay_awake()
        s.chunks.clear()
        s.wake_engine.reset()

    def feed_wake_detector(self, chunk: Any) -> None:
        s = self.s
        if self.wake_prev_awake:
            s.wake_engine.reset()
            self.wake_prev_awake = False
        for event in s.wake_engine.feed(chunk):
            self.after_wake(event)

    # --- capturing an utterance ----------------------------------------------------------------
    def reset_capture(self, keep_speech_start: bool = False) -> None:
        s = self.s
        self.buffer, s.recording, self.silence_started = [], False, None
        if not keep_speech_start:
            self.speech_started = None
            self.voiced_run = self.longest_voiced_run = 0
        s.pre_roll.clear()

    def on_voiced(self, chunk: Any, now: float, awake: bool) -> None:
        s = self.s
        if not s.recording:
            s.recording, self.speech_started = True, now
            self.utterance_awake = awake
            self.voiced_run = self.longest_voiced_run = 0
            self.buffer = list(s.pre_roll)
            s.pre_roll.clear()
        else:
            self.buffer.append(chunk)
        self.voiced_run += 1
        self.longest_voiced_run = max(self.longest_voiced_run, self.voiced_run)
        self.silence_started = None

    def on_silent(self, chunk: Any, now: float) -> None:
        self.voiced_run = 0
        self.buffer.append(chunk)
        if self.silence_started is None:
            self.silence_started = now
        elif now - self.silence_started > self.s.silence_len:
            self.end_utterance(now)

    def drop(self, reason: str, message: str, **fields) -> None:
        self.s.rec_event("drop", reason=reason, **fields)
        log.console(message, flush=True)

    def end_utterance(self, now: float) -> None:
        """Silence ended the utterance: recognize it and, if it is a request, answer it."""
        s = self.s
        s.voice_finished_at = self.silence_started
        duration = now - self.speech_started
        audio = np.concatenate(self.buffer)
        self.reset_capture(keep_speech_start=True)
        # Endpoint silence must not turn a one-frame noise click into an apparently long
        # utterance. Preserve short answers.
        if duration < MIN_SPEECH or self.longest_voiced_run < 2:
            return
        if os.environ.get("JULIUS_SAVE_AUDIO_DIR"):
            save_utterance(s, audio)
        text, lang, no_speech = transcribe_input(s, audio, wake=not self.utterance_awake)
        if text and not re.search(r"\w", text):
            text = ""  # "!" is Whisper's guess at noise, not speech
        if self.is_noise_or_echo(text, no_speech, duration):
            s.chunks.clear()
            return
        self.handle_transcript(text, lang)

    def is_noise_or_echo(self, text: str, no_speech: float, duration: float) -> bool:
        s = self.s
        if not text or no_speech > NO_SPEECH_LIMIT:
            what = "nothing transcribed" if not text else "probably not speech"
            self.drop(
                "no_speech",
                f"  [ignored: {what} (no-speech {no_speech:.2f}, {duration:.1f}s)]",
                no_speech=round(float(no_speech), 3),
                dur=round(duration, 2),
            )
            return True
        if s.self_echo.is_echo(text):
            self.drop(
                "echo",
                f"  [dropped as echo of own voice: {len(text.split())} words, {duration:.1f}s]",
                text=text,
                dur=round(duration, 2),
            )
            return True
        return False

    def require_wake_word(self, text: str) -> str | None:
        """The request without the wake word, or None when he was not addressed."""
        s = self.s
        if not self.utterance_awake:
            if not has_wake(text):
                self.drop(
                    "no_wake", f"  [ignored: no wake word ({len(text.split())} words)]", text=text
                )
                return None
            log.console("  [awake]")
            return strip_wake(text)
        if s.wake_engine is not None and has_wake(text):
            s.wake_engine.note_spoken_wake(text)
        return strip_leading_wake(
            text
        )  # "Julius, what about tomorrow?" still reaches the follow-up router

    def merge_held(self, text: str) -> str:
        """The rest of a sentence he paused in is joined to what he said before."""
        s = self.s
        held = s.held
        if held and time.time() - held["t"] <= HOLD_SECONDS:
            text = f"{held['text']} {text}".strip()
            s.held = None
            s.rec_event("merge", text=text)
        elif held:
            s.held = None
        return text

    def hold_unfinished(self, text: str) -> bool:
        """A sentence that stops mid-thought waits for its rest."""
        s = self.s
        if not (dangling(text) and not listening_control(text)):
            return False
        s.held = {"text": text, "t": time.time()}
        self.stay_awake()
        s.rec_event("hold", text=text)
        log.console(
            f"  [waiting for the rest of the sentence ({len(text.split())} words so far)]",
            flush=True,
        )
        s.chunks.clear()
        return True

    def only_stop_word(self, text: str) -> bool:
        """ "Stop" with nothing pending is just a stop: no turn."""
        s = self.s
        pending = getattr(s.file_session, "pending", None) or s.code_session.pending
        if not stop_only(text) or pending:
            return False
        log.console("  [stopped]", flush=True)
        s.rec_event("stop_word", text=text)
        s.rec_event("reply", text="", control="stop")
        self.finish_exchange()
        return True

    def handle_transcript(self, text: str, lang: str) -> None:
        """A recognized utterance: wake gating, merging, sleep/wake commands, then a turn."""
        s = self.s
        text = self.require_wake_word(text)
        if text is None:
            return
        text = self.merge_held(text)
        if self.hold_unfinished(text):
            return
        control = listening_control(text)
        if control:
            self.apply_control(text, control)
            s.chunks.clear()
            return
        if not self.utterance_awake and len(text.split()) < 2:
            self.say_yeah()
            s.chunks.clear()
            return
        if self.only_stop_word(text):
            return
        process(s, drop_stop_lead(text), lang)
        talked_over, s.talkover_pending = s.talkover_pending, False
        if not talked_over:
            s.chunks.clear()
        self.stay_awake()

    def process_chunk(self, awake: bool) -> None:
        """One microphone block: capture bookkeeping, wake detection, voiced/silent handling."""
        s = self.s
        chunk, now = s.chunks.popleft()
        level = float(np.abs(chunk).mean())
        if s.capture_clock.discontinuous(now):
            self.reset_capture()
            if s.wake_engine is not None:
                s.wake_engine.reset()  # a gap (Julius speaking) must not stitch old audio to the echo tail
            log.console("  [audio capture resumed; partial input discarded]", flush=True)
        if s.recording and len(self.buffer) >= MAX_UTTERANCE_CHUNKS:
            self.reset_capture(keep_speech_start=True)
            s.chunks.clear()
            log.console("  [capture exceeded 30 seconds; discarded incomplete input]", flush=True)
            return
        if not s.recording:
            s.pre_roll.append(chunk)
        if s.wake_engine is not None:
            if awake:
                self.wake_prev_awake = True
            elif not s.recording:
                self.feed_wake_detector(chunk)
                return
        if level > s.threshold:
            self.on_voiced(chunk, now, awake)
        elif s.recording:
            self.on_silent(chunk, now)

    # --- the loop ------------------------------------------------------------------------------
    def step(self) -> None:
        """One pass of the loop."""
        s = self.s
        self.expire_wake()
        awake = time.time() < s.awake_until
        self.poll_reminders()
        if s.typed:
            self.handle_typed()
            return
        if self.answer_held():
            return
        if awake and not s.recording:
            self.vision_check_in()
        if self.announce_noted() or self.announce_code_result():
            return
        if not s.chunks:
            time.sleep(0.01)
            return
        self.process_chunk(awake)

    def run(self) -> None:
        """Listen until Ctrl+C or `/quit`, then wrap up."""
        from julius.runtime.shutdown import wrap_up

        s = self.s
        with microphone_stream(s):
            try:
                while True:
                    self.step()
            except KeyboardInterrupt:
                wrap_up(s)
