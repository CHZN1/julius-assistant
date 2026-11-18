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


TYPED_HOLD_S = 0.8  # a typed message waits this long before its turn: time to edit (up arrow) or cancel (Esc), and for a quick correction to replace it
REPEAT_S = 5.0  # the same text again this soon after it was picked up is a repeat, not a new turn
_typed_lock = threading.Lock()


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split()).strip(" .!?")


def _waiting_ui(s: Session) -> Any:
    last = s.typed[-1] if s.typed else None
    return last if getattr(last, "tid", None) else None


def ui_typed(s: Session, text: str) -> dict:
    """Text typed in the orb. It shows in the transcript at once (pending), stops whatever Julius is saying or doing (like talking over him), and is
    processed after a short hold. A message still waiting is replaced by a newer one (the latest wins: "new" then "nes"), and the same text again is
    dropped, so rapid repeats never queue up as separate turns."""
    import secrets

    from julius.runtime import controls

    t = str(text).strip()
    now = time.monotonic()
    with _typed_lock:
        waiting = _waiting_ui(s)
        cur = getattr(s, "current_typed", None)
        if waiting is not None and _norm(waiting) == _norm(t):
            log.event("ui_typed", stage="repeat_dropped")
            return {"queued": True, "id": waiting.tid, "merged": "repeat"}
        if (
            waiting is None
            and cur is not None
            and _norm(cur) == _norm(t)
            and now - getattr(s, "current_typed_at", 0.0) < REPEAT_S
        ):
            log.event("ui_typed", stage="repeat_dropped")
            return {"queued": False, "id": getattr(cur, "tid", ""), "merged": "repeat"}
        entry = uichannel.TypedText(t)
        entry.queued_at = now
        if (
            waiting is not None
        ):  # a correction while the first is still waiting: it takes the first one's place
            entry.tid = waiting.tid
            s.typed[-1] = entry
            merged = "replaced"
        else:
            entry.tid = secrets.token_hex(4)
            s.typed.append(entry)
            merged = ""
    if s.events_hub is not None:
        s.events_hub.say("you", t, None, id=entry.tid, pending=True)
    controls.stop_now(s, "typed")  # only acts while a response is running
    log.event("ui_typed", stage="queued", queue=len(s.typed), merged=merged or None)
    return {"queued": True, "id": entry.tid, **({"merged": merged} if merged else {})}


def ui_typed_change(s: Session, tid: str, text: str | None) -> dict:
    """Edit (text) or cancel (text None) a typed message that has not been picked up yet."""
    with _typed_lock:
        for i, entry in enumerate(list(s.typed)):
            if getattr(entry, "tid", None) == tid:
                if text is None:
                    del s.typed[i]
                    new = None
                else:
                    new = uichannel.TypedText(str(text).strip())
                    new.tid, new.queued_at = tid, time.monotonic()
                    s.typed[i] = new
                break
        else:
            return {"ok": False, "error": "That message was already sent."}
    if s.events_hub is not None:
        if new is None:
            s.events_hub.say("you", "", None, id=tid, cancelled=True)
        else:
            s.events_hub.say("you", new, None, id=tid, pending=True)
    log.event("ui_typed", stage="cancelled" if new is None else "edited")
    return {"ok": True, "id": tid}


def ui_photo(s: Session, jpeg: bytes, auto: bool = False) -> dict:
    """A photo from the orb's camera window (Use Photo, or the automatic use after the shot): held in memory, shown as a still, and the follow-up ("make it pixel art") queued
    like a typed line."""
    from julius.art import camera

    then = camera.received(
        jpeg, getattr(s, "panel_hub", None), getattr(s, "panel_files", None), s.turn, auto
    )
    if then:
        entry = uichannel.TypedText(then)
        entry.queued_at = time.monotonic()
        s.typed.append(entry)
    return {"held": True, "then": bool(then)}


def ui_sprite_get() -> dict | None:
    """The sprite being edited, as layers for the orb's editor window (or None)."""
    from julius.art import sprite_doc

    doc = sprite_doc.STUDIO.get()
    return sprite_doc.editor_view(doc) if doc else None


def ui_sprite_post(edits: Any, sprite_id: str) -> tuple[bool, str]:
    """Pixel edits from the editor window: checked (layers, canvas, palette) and saved as one undo step; the card updates."""
    from julius.art import tool

    return tool.sprite_pixels(edits, sprite_id)


def ui_sprite_ops(sprite_id: str, ops: Any) -> dict:
    """The sprite card's buttons: applied and rendered straight away, never a turn (no transcript, no speech, no model)."""
    from julius.art import tool

    return tool.sprite_ops(sprite_id, ops)


def ui_conversation(s: Session, body: dict) -> dict:
    """The conversations sidebar: switch, new, rename, pin, move, delete (to the trash), restore, search, forget a note. Never under a turn:
    a switch asked while Julius is answering happens when that turn ends."""
    conv = getattr(s, "conversations", None)
    if conv is None:
        return {"ok": False, "error": "conversations are off"}
    from julius.conversations import channel

    busy = bool(getattr(s, "turn_active", False))
    return channel.op(conv, body, busy=busy, history=s.history)


def ui_camera(s: Session, event: str) -> dict:
    """What happened to the orb's camera window: closed (a pending follow-up is dropped) or denied (the camera buttons say why)."""
    from julius.art import camera

    if event == "closed":
        camera.cancel_pending()
    elif event == "denied" and getattr(s, "panel_hub", None) is not None:
        camera.cancel_pending()
        s.panel_hub.set_camera_status(
            False,
            "Camera permission is off. Allow it in System Settings, Privacy and Security, Camera.",
            "permission",
        )
    return {}


def start_ui_channel(s: Session) -> None:
    """Open the orb's local channel (typed text and file drops), unless running without devices."""
    if s.no_devices and not s.demo:
        return
    try:
        s.drop_stage = dropstage.DropStage(
            lambda p: files.agent().resolve(p), lambda p: files.agent().mode_of(p)
        )
        handlers = {
            "typed": lambda t: ui_typed(s, t),
            "typed_change": lambda tid, text: ui_typed_change(s, tid, text),
            "drop": lambda p: ui_drop(s, p),
            "photo": lambda data, auto=False: ui_photo(s, data, auto),
            "camera": lambda event: ui_camera(s, event),
            "sprite_get": ui_sprite_get,
            "sprite_post": ui_sprite_post,
            "sprite_ops": ui_sprite_ops,
            "conversation": lambda body: ui_conversation(s, body),
        }
        s.ui_channel = uichannel.start_from_config(config.load_config().get("ui"), handlers)
    except Exception as exc:
        s.ui_channel = None
        log.console(f"  [ui channel unavailable: {type(exc).__name__}: {exc}]", flush=True)
