"""Deterministic command handlers that answer before the brain is asked, and background upkeep."""

import contextlib
import os
import threading
import time

import julius.agents.files as jf
import julius.brain.tools as jt
from julius import config, log
from julius.audio.asr_resources import recognition_resources
from julius.brain.backend import LLM_BACKEND, close_backend, llm_chat
from julius.runtime.session import Session
from julius.runtime.speech import CLONE_BACKENDS, print_tool, speak


def release_idle_models(s: Session) -> bool:
    """Sleeping keeps imports available, but releases loaded model weights."""
    try:
        s.tts.suspend()
        close_backend()
        resident = os.environ.get("JULIUS_ASR_RESIDENT") == "1"
        with recognition_resources(not resident):
            pass
        if os.environ.get("JULIUS_KEEP_VOICE") == "1" and LLM_BACKEND == "cloud":
            released = "" if resident else "; speech recognition released"
            print(f"  [idle: the voice stays loaded{released}]", flush=True)
        else:
            print("  [idle model weights released]", flush=True)
        return True
    except Exception as exc:
        print(
            f"  [idle release failed: {type(exc).__name__}; "
            "release will retry before the next model phase]",
            flush=True,
        )
        return False


def announce_reminder(s: Session, message: str) -> None:
    """Speak a reminder that has come due."""
    print(f"  JULIUS REMINDER: {message}", flush=True)
    if s.tts.backend in CLONE_BACKENDS:
        close_backend()
    speak(s, message)


def _complete(prompt: str) -> str:
    reply = llm_chat(
        messages=[{"role": "user", "content": prompt}],
        think=False,
        options={"temperature": 0.0, "num_predict": 200},
    )
    return reply.message.content or ""


def _capture_facts(s: Session, text: str) -> None:
    """Note lasting facts from `text`; one short "Noted." follows when you are not talking."""
    stored = s.memory.autocapture(text, _complete)
    for row in stored:
        s.memory.last_added = row
        print(f"  [noted: {row['text']}]", flush=True)
        with contextlib.suppress(Exception):
            log.event("memory", "auto", id=row["id"])
    if not stored or not (config.load_config().get("memory") or {}).get("announce_auto", True):
        return
    s.noted_pending = time.time()
    if s.no_devices:
        s.noted_pending = None
        with contextlib.suppress(Exception):
            speak(s, "Noted.")


def memory_autocapture(s: Session, text: str) -> None:
    """Lasting facts you state about yourself are noted automatically (marked auto; say
    'forget that' to remove one). Runs off the reply path."""
    if s.memory is None or not s.cloud or not s.memory.worth_a_look(text):
        return
    if s.no_devices:
        _capture_facts(s, text)
    else:
        threading.Thread(target=_capture_facts, args=(s, text), daemon=True).start()


def handle_memory(s: Session, text: str) -> str | None:
    """remember / forget / what do you remember: deterministic, no model call."""
    try:
        return s.memory.handle(text)
    except Exception as exc:
        print(f"  [memory command failed: {type(exc).__name__}: {exc}]", flush=True)
        return None


def handle_code(s: Session, text: str) -> str | None:
    """The coding agent's voice commands: stage, yes, status, cancel, show changes, merge, undo,
    discard. No model call."""
    try:
        out = s.code_session.handle(text, s.turn)
    except Exception as exc:
        print(f"  [coding command failed: {type(exc).__name__}: {exc}]", flush=True)
        return None
    if out is None:
        return None
    s.file_session.pending = None
    s.intent_router.last = None
    for line in (out.get("data") or {}).get("lines", [])[:40]:
        print(f"    {line}", flush=True)
    print(f"  [coding agent: {out['code']}]", flush=True)
    return out["message"]


def _set_idle_yes(s: Session) -> None:
    """A bare yes right after a question Julius asked belongs to the conversation, not to a controller."""
    try:
        last = [m for m in s.history if m.get("role") == "assistant"][-1]["content"]
        s.file_session.suppress_idle_yes = str(last).rstrip().endswith("?")
    except Exception:
        s.file_session.suppress_idle_yes = False


def _utility_reply(s: Session, text: str) -> str | None:
    out = jt.dispatcher().human_utilities(s.utility_session, text, s.turn)
    if out is None:
        return None
    s.file_session.pending = None
    s.intent_router.last = None
    print_tool("planning", out["message"])
    if out["code"] == "listed" and len(out["data"].get("items", [])) > 5:
        first = "\n".join(out["message"].splitlines()[:5])
        return first + " The remaining items are on screen."
    return out["message"]


def _file_reply(s: Session, text: str) -> str | None:
    out = jt.dispatcher().human_file(s.file_session, text, s.turn)
    if out is None:
        return None
    s.intent_router.last = None
    print_tool("file_agent", jf.render(out))
    if out["code"] == "extracted":
        more = " The remaining extracted text and citations are on screen."
        return out["message"][:500] + (more if len(out["message"]) > 500 else "")
    if out["code"] == "read":
        body = out["data"]["text"]
        more = " The rest is shown on screen." if len(body) > 240 else ""
        return out["message"] + " " + body[:240] + more
    return out["message"]


def handle_files(s: Session, text: str) -> str | None:
    """Planner, reminder, calendar and file commands: deterministic, no model call."""
    _set_idle_yes(s)
    reply = _utility_reply(s, text)
    return reply if reply is not None else _file_reply(s, text)
