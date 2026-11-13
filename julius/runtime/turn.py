"""One conversational turn: pre-turn hooks, routing, answer (shortcut or brain), and the report."""

import json
import resource
import time
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import Mock

import julius.agents.files as jf
import julius.brain.tools as jt
import julius.memory.sessions as jm
from julius import log
from julius.audio.cancellation import TurnCancelled
from julius.audio.listening import incomplete_fragment
from julius.brain import orchestrator
from julius.brain.backend import (
    LLM,
    LLM_BACKEND,
    close_backend,
    generation_metrics,
    keep_brain_loaded,
    release_metrics,
)
from julius.brain.conversation import authority_discussion_reply
from julius.runtime.brain_turn import ask_streaming
from julius.runtime.handlers import (
    handle_code,
    handle_files,
    handle_memory,
    handle_panel,
    handle_vision,
    memory_autocapture,
)
from julius.runtime.session import Session
from julius.runtime.speech import CLONE_BACKENDS, speak, speak_sentence

NOT_CAUGHT = "I didn't catch the full thought. Could you say that again?"


@dataclass
class Turn:
    """What is known about the turn in progress."""

    text: str
    lang: str
    started: float
    vision: dict = field(default_factory=dict)
    vision_line: str = ""
    routed: str | None = None
    reply: str = ""
    error: str | None = None

    @property
    def typed(self) -> bool:
        return self.lang == "typed"

    @property
    def answer(self) -> str:
        """The text that was answered, whichever path produced it."""
        return self.routed if self.routed is not None else self.reply


def process(s: Session, text: str, lang: str) -> None:
    """Handle one request (spoken or typed) from first hook to final report."""
    s.turn_active = True
    try:
        with s.cancellation.activity():
            run_turn(s, text, lang)
    finally:
        s.turn_active = False


def run_turn(s: Session, text: str, lang: str) -> None:
    turn = begin_turn(s, text, lang)
    route(s, turn)
    if turn.routed is not None:
        answer_routed(s, turn)
    else:
        answer_with_brain(s, turn)
    finish_turn(s, turn)


# --- before the answer ---------------------------------------------------------------------------
def reset_timings(s: Session, started: float, lang: str) -> None:
    s.noted_pending = None
    s.response_started = started
    s.first_audio_at = None
    s.reasoning_seconds = None
    s.brain_first_token_seconds = None
    s.reasoning_release_seconds = None
    if lang == "typed":
        s.voice_finished_at = None


def cap_notice(s: Session) -> None:
    """Say so when the daily spending cap changed what he can do."""
    notice = s.cloud_brain.cap_notice()
    if not notice:
        return
    log.console(f"  JULIUS: {notice}", flush=True)
    try:
        speak(s, notice)
    except Exception as exc:
        log.console(f"  [speech unavailable: {exc}]", flush=True)


def begin_turn(s: Session, text: str, lang: str) -> Turn:
    """Reset per-turn state, look at the room, tell the agents a new turn started."""
    started = time.perf_counter()
    reset_timings(s, started, lang)
    snapshot = s.eyes.snapshot()
    turn = Turn(text, lang, started, snapshot, s.eyes.describe(snapshot))
    log.console(f"  YOU [{lang}]: {text}")
    if s.events_hub is not None:
        s.events_hub.say("you", text, s.turn + 1)
    if turn.vision_line:
        log.console(f"  (sees: {turn.vision_line})")
    s.turn += 1
    s.rec_event("turn", n=s.turn, text=text, lang=lang)
    jf.set_turn(s.turn)
    jt.dispatcher().begin_turn()
    if s.cloud:
        s.cloud_tracker.begin_turn()
        orchestrator.configure(user_text=text)
        cap_notice(s)
    return turn


def route(s: Session, turn: Turn) -> None:
    """Pick a shortcut reply (undo, read-back, memory, code, files) or leave `routed` empty for the brain."""
    if s.undo_coordinator is not None:
        s.undo_coordinator.observe(
            s.turn
        )  # whatever changed since the last turn belongs to that turn
    read_back = None
    if s.lowconf_gate is not None:
        # Shaky recognition + a risky request: read it back and wait for a yes.
        _, turn.text, read_back = s.lowconf_gate.step(
            turn.text, s.last_asr_confidence, voice=not turn.typed
        )
    undo_said = None
    if not read_back and s.undo_coordinator is not None:
        undo_said = s.undo_coordinator.handle(turn.text)  # bare "undo": the latest action, named
    turn.routed = undo_said or read_back or _command_reply(s, turn.text)


def _command_reply(s: Session, text: str) -> str | None:
    discussion = authority_discussion_reply(text)
    if discussion or incomplete_fragment(text):
        return discussion or NOT_CAUGHT
    return (
        handle_panel(s, text)
        or handle_vision(s, text)
        or handle_memory(s, text)
        or handle_code(s, text)
        or handle_files(s, text)
    )


# --- the answer ----------------------------------------------------------------------------------
def spoken_form(routed: str) -> str:
    """The planner's results have a shorter spoken form than the text shown on screen."""
    result = jt.dispatcher().last_result or {}
    if result.get("agent") == "planning":
        return result.get("data", {}).get("speech_message", routed)
    return routed


def answer_routed(s: Session, turn: Turn) -> None:
    """Speak a shortcut reply and record it."""
    log.console(f"  JULIUS: {turn.routed}\n", flush=True)
    try:
        speak(s, spoken_form(turn.routed))
    except TurnCancelled:
        turn.error = "cancelled"
        log.console("  [response stopped; completed actions remain completed]", flush=True)
    except Exception as exc:
        turn.error = f"{type(exc).__name__}: {exc}"
        log.console(f"  [speech unavailable: {exc}; result shown as text]", flush=True)
    s.history.append({"role": "user", "content": turn.text})
    s.history.append({"role": "assistant", "content": turn.routed})
    jm.log_turn(turn.text, turn.routed)


def record_reasoning(s: Session, started: float) -> None:
    """Reasoning time includes sentence playback; first-token time is the brain's own latency."""
    s.reasoning_seconds = time.perf_counter() - started
    first = s.first_token_at
    s.brain_first_token_seconds = (first - started) if first is not None else None


def ask_with_separate_voice(s: Session, turn: Turn) -> str:
    """Local brain with a heavy voice: reason first, release the brain, then speak the whole reply."""
    segments: list[str] = []  # sentences are collected silently; the whole reply is spoken below
    started = time.perf_counter()
    s.first_token_at = None
    try:
        reply = ask_streaming(
            s, turn.text, turn.vision_line, speak_fn=segments.append, before_inference=s.tts.suspend
        )
    finally:
        record_reasoning(s, started)
        release_started = time.perf_counter()
        if not keep_brain_loaded():
            close_backend()
        s.reasoning_release_seconds = time.perf_counter() - release_started
    speak(s, reply)
    return reply


def ask_with_streamed_voice(s: Session, turn: Turn) -> str:
    """Speak each sentence as it streams from the brain."""
    started = time.perf_counter()
    s.first_token_at = None
    try:
        return ask_streaming(
            s, turn.text, turn.vision_line, speak_fn=lambda t: speak_sentence(s, t)
        )
    finally:
        record_reasoning(s, started)


def answer_with_brain(s: Session, turn: Turn) -> None:
    """Ask the brain, speak the answer, and keep the exchange (and its tool results) in the history."""
    try:
        if s.tts.backend in CLONE_BACKENDS and not s.cloud_brain:
            turn.reply = ask_with_separate_voice(s, turn)
        else:
            turn.reply = ask_with_streamed_voice(s, turn)
    except TurnCancelled:
        turn.error = "cancelled"
        turn.reply = "Response stopped by user."
        log.console("  [response stopped; completed actions remain completed]", flush=True)
    except Exception as exc:
        turn.error = f"{type(exc).__name__}: {exc}"
        log.console(f"  [turn failed: {type(exc).__name__}: {exc}]", flush=True)
        turn.reply = "I couldn't complete that request. Please try again."
        try:
            speak(s, turn.reply)
        except Exception as speech_error:
            log.console(f"  [speech unavailable: {speech_error}; reply shown as text]", flush=True)
    remember_exchange(s, turn)
    jm.log_turn(
        turn.text,
        turn.reply,
        {"emotion": turn.vision.get("emotion"), "conf": turn.vision.get("conf")},
    )
    if turn.error is None:
        memory_autocapture(s, turn.text)


def remember_exchange(s: Session, turn: Turn) -> None:
    """Keep the tool call and its result in the history: a factual answer with no lookup behind
    it makes the next turn accuse itself of making it up."""
    s.history.append({"role": "user", "content": turn.text})
    carried, s.turn_messages = s.turn_messages, None
    if carried and s.cloud:
        messages, start = carried
        s.history.extend(
            m for m in messages[start:] if m.get("role") == "tool" or m.get("tool_calls")
        )
    # The model must not read its own "Response stopped by user." as a complaint.
    shown = "[interrupted by the user]" if turn.error == "cancelled" else turn.reply
    s.history.append({"role": "assistant", "content": shown})


# --- after the answer ----------------------------------------------------------------------------
def timing_line(s: Session, turn: Turn) -> str:
    """One machine-readable line per turn (no transcript text, so the redacted service log keeps it)."""
    first, finished = s.first_audio_at, s.voice_finished_at
    confidence = {} if turn.typed else (s.last_asr_confidence or {})
    logprobs = [x for x in (confidence.get("segment_log_probabilities") or []) if x is not None]
    no_speech = confidence.get("no_speech_probability")
    asr = s.last_asr_seconds
    parts = [
        f"first_audio={first - turn.started:.2f}" if first is not None else "first_audio=none",
        f"speech_end_to_audio={first - finished:.2f}"
        if first is not None and finished is not None and not turn.typed
        else "speech_end_to_audio=na",
        f"asr={asr:.2f}" if asr is not None and not turn.typed else "asr=na",
        f"asr_logprob={min(logprobs):.2f}" if logprobs else "asr_logprob=na",
        f"no_speech={no_speech:.2f}" if no_speech is not None else "no_speech=na",
        f"total={time.perf_counter() - turn.started:.2f}",
        f"input={'typed' if turn.typed else 'voice'}",
        f"routed={'shortcut' if turn.routed is not None else 'brain'}",
        f"error={'yes' if turn.error else 'no'}",
    ]
    return " ".join(parts)


def report_timing(s: Session, turn: Turn) -> None:
    try:
        line = timing_line(s, turn)
        log.console("  [timing: " + line + "]", flush=True)
        s.rec_event("reply", n=s.turn, text=turn.answer, timing=line)
    except Exception:
        pass


def trim_history(history: list[dict]) -> None:
    """Keep the system prompt and the last eight messages, never starting mid tool exchange."""
    if len(history) > 9:
        history[:] = [history[0]] + history[-8:]
    while len(history) > 1 and history[1].get("role") != "user":
        history.pop(1)


def speech_worker_metrics(tts: Mock) -> dict:
    keep = {"delivery_mode", "voice_strategy", "dtype", "generation_chunks", "allocation_retries"}
    metrics = getattr(tts, "worker_metrics", {})
    return {k: v for k, v in metrics.items() if k.endswith("_bytes") or k in keep}


def validation_row(s: Session, turn: Turn, underflows: list) -> dict:
    """One record of this turn for the validation output file (evals read it)."""
    first, finished = s.first_audio_at, s.voice_finished_at
    tracker = s.cloud_tracker
    last_result = jt.dispatcher().last_result
    from_files = turn.routed is not None and last_result and last_result["agent"] == "files"
    return {
        "prompt": turn.text,
        "answer": turn.answer,
        "asr_confidence": None if turn.typed else s.last_asr_confidence,
        "asr_seconds": None if turn.typed else s.last_asr_seconds,
        "processing_to_first_audio_seconds": first - turn.started if first is not None else None,
        "end_of_speech_to_first_audio_seconds": first - finished
        if first is not None and finished is not None
        else None,
        "reasoning_generation_metrics": generation_metrics(),
        "reasoning_release_metrics": release_metrics(),
        "brain_first_token_seconds": s.brain_first_token_seconds,
        "audio_underflows": len(underflows),
        "audio_underflow_events": underflows,
        "cost_by_tier_usd": tracker.turn_costs() if tracker is not None else None,
        "cost_today_usd": tracker.today_total() if tracker is not None else None,
        "reasoning_seconds": s.reasoning_seconds,
        "reasoning_release_seconds": s.reasoning_release_seconds,
        "error": turn.error,
        "seconds": time.perf_counter() - turn.started,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "dispatcher_tasks": list(jt.dispatcher().turn_tasks),
        "file_result": s.file_session.last_result if from_files else None,
        "vision": turn.vision,
        "tts": s.tts.backend,
        "backend": LLM_BACKEND,
        "model": LLM,
        "speech_worker": speech_worker_metrics(s.tts),
    }


def record_validation(s: Session, turn: Turn, underflows: list) -> None:
    s.validation_turns.append(validation_row(s, turn, underflows))
    Path(s.validation_output).write_text(json.dumps(s.validation_turns, indent=2))


def finish_turn(s: Session, turn: Turn) -> None:
    """Audio underflows, the timing line, the cost line, history trimming, validation output."""
    underflows = s.speech_player.pop_events() if s.speech_player is not None else []
    if underflows:
        log.console(f"  [audio underflows this turn: {len(underflows)}]", flush=True)
    report_timing(s, turn)
    if s.cloud_tracker is not None:
        log.console(s.cloud_tracker.status_line(), flush=True)
    trim_history(s.history)
    if s.validation_output:
        record_validation(s, turn, underflows)
    s.response_started = None
