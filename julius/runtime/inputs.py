"""Typed input: the terminal, scripted validation runs, and the orb's text and file drops."""

import json
import threading
import time
from pathlib import Path
from typing import Any

from julius import config, log
from julius.agents import dropstage, files
from julius.runtime.controls import stop_now
from julius.runtime.session import Session
from julius.ui_bridge import uichannel

STOP_WORDS = {"/stop", "stop", "cancel response"}


def read_terminal(s: Session) -> None:
    """Thread body: queue each line typed in the terminal (`/stop` interrupts, `/quit` ends)."""
    while True:
        try:
            command = input().strip()
        except (EOFError, KeyboardInterrupt):
            return
        if command.lower() in STOP_WORDS:
            if s.cancellation.busy.is_set():
                stop_now(s, "typed")
            else:
                log.console("  [no response is active]", flush=True)
        elif command:
            if command == "/quit":
                s.cancellation.request()
            s.typed.append("what do you remember" if command == "/memories" else command)


def start_terminal(s: Session) -> None:
    """Queue the validation script when there is one, then read the terminal in a thread."""
    if s.scripted:
        s.typed.extend(json.loads(Path(s.scripted).read_text()))
        s.typed.append("/quit")
    threading.Thread(target=read_terminal, args=(s,), daemon=True).start()


def ui_drop(s: Session, paths: Any) -> dict:
    """Files dropped on the orb: validated and staged, then Julius says what he got and asks what to
    do. Nothing is opened or changed."""
    result = s.drop_stage.stage(paths)
    s.typed.append(dropstage.DropNotice(result["notice"]))
    return {key: result[key] for key in ("accepted", "rejected", "expires_in")}


def ui_typed(s: Session, text: str) -> dict:
    """Text typed in the orb: queued exactly like a line typed in the terminal, so it takes the same
    path as speech (and wakes him for that turn)."""
    entry = uichannel.TypedText(text)
    entry.queued_at = time.monotonic()
    s.typed.append(entry)
    log.event("ui_typed", stage="queued", queue=len(s.typed))
    return {"queued": True}


def start_ui_channel(s: Session) -> None:
    """Open the orb's local channel (typed text and file drops), unless running without devices."""
    if s.no_devices:
        return
    try:
        s.drop_stage = dropstage.DropStage(
            lambda p: files.agent().resolve(p), lambda p: files.agent().mode_of(p)
        )
        handlers = {"typed": lambda t: ui_typed(s, t), "drop": lambda p: ui_drop(s, p)}
        s.ui_channel = uichannel.start_from_config(config.load_config().get("ui"), handlers)
    except Exception as exc:
        s.ui_channel = None
        log.console(f"  [ui channel unavailable: {type(exc).__name__}: {exc}]", flush=True)
