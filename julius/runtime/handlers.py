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
            log.console(f"  [idle: the voice stays loaded{released}]", flush=True)
        else:
            log.console("  [idle model weights released]", flush=True)
        return True
    except Exception as exc:
        log.console(
            f"  [idle release failed: {type(exc).__name__}; "
            "release will retry before the next model phase]",
            flush=True,
        )
        return False


def announce_reminder(s: Session, message: str) -> None:
    """Speak a reminder that has come due."""
    log.console(f"  JULIUS REMINDER: {message}", flush=True)
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
        log.console(f"  [noted: {row['text']}]", flush=True)
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


def handle_panel(s: Session, text: str) -> str | None:
    """Side-panel commands (open source N, save this card, close the panel): deterministic, no model call; they act on the stored card."""
    if getattr(s, "panel_hub", None) is None:
        return None
    try:
        from julius.ui_bridge import panel_emit

        saved = (config.load_config().get("ui") or {}).get("saved_dir")
        return panel_emit.handle(s.panel_hub, s.panel_files, text, saved)
    except Exception as exc:
        log.console(f"  [panel command failed: {type(exc).__name__}: {exc}]", flush=True)
        return None


def handle_camera(s: Session, text: str) -> str | None:
    """ "Take a photo", "take a selfie", "take a selfie and make it pixel art": opens the orb's camera window with the countdown running. No model call, no upload."""
    from julius.art import camera

    req = camera.parse(text)
    if req is None:
        return None
    try:
        return camera.request(
            config.load_config().get("vision") or {}, getattr(s, "panel_hub", None), req["then"]
        )
    except Exception as exc:
        log.console(f"  [camera failed: {type(exc).__name__}: {exc}]", flush=True)
        return "I couldn't open the camera."


def handle_vision(s: Session, text: str) -> str | None:
    """ "What am I holding", "what's on my screen", "look at this image": one picture, only on request, answered by Claude vision and shown in a card."""
    from julius.agents import vision

    req = vision.parse(text, has_dropped=bool(s.drop_stage and s.drop_stage.pending()))
    if req is None:
        return None
    try:
        import julius.brain.cloud as jc

        cfg = config.load_config().get("vision") or {}
        return vision.look(
            req,
            brain=jc.get_brain(),
            panel=getattr(s, "panel_hub", None),
            files=getattr(s, "panel_files", None),
            turn=s.turn,
            drop_stage=s.drop_stage,
            cfg=cfg,
        )
    except Exception as exc:
        log.console(f"  [vision failed: {type(exc).__name__}: {exc}]", flush=True)
        return "I couldn't look at that."


def handle_music(s: Session, text: str) -> str | None:
    """ "Rescan my samples", "convert kick.wav to mp3", "turn this photo into pixel art": answered directly by a fixed pattern, no model call."""
    try:
        from julius.art import commands as art_commands
        from julius.music import commands

        return commands.handle(text) or commands.convert_reply(s, text) or art_commands.handle(text)
    except Exception as exc:
        log.console(f"  [audio command failed: {type(exc).__name__}: {exc}]", flush=True)
        return "I couldn't do that audio request."


def handle_memory(s: Session, text: str) -> str | None:
    """remember / forget / what do you remember: deterministic, no model call."""
    try:
        return s.memory.handle(text)
    except Exception as exc:
        log.console(f"  [memory command failed: {type(exc).__name__}: {exc}]", flush=True)
        return None


def handle_code(s: Session, text: str) -> str | None:
    """The coding agent's voice commands: stage, yes, status, cancel, show changes, merge, undo,
    discard. No model call."""
    try:
        out = s.code_session.handle(text, s.turn)
    except Exception as exc:
        log.console(f"  [coding command failed: {type(exc).__name__}: {exc}]", flush=True)
        return None
    if out is None:
        return None
    s.file_session.pending = None
    s.intent_router.last = None
    for line in (out.get("data") or {}).get("lines", [])[:40]:
        log.console(f"    {line}", flush=True)
    log.console(f"  [coding agent: {out['code']}]", flush=True)
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
