"""Take a photo with the camera, for art: "take a photo", "take a selfie", "take a selfie and make it pixel art".

The orb app owns the camera. Julius asks it (a `panel.camera_open` feed message) to open its floating camera window: a live mirrored preview, a shutter, a 3-second
countdown, then Retake or Use Photo. Closing the window or Esc stops the camera at once, so the camera light is off whenever the window is closed. Use Photo sends the
JPEG to `POST /v1/photo` and Julius holds it in memory (see `source.hold`), where it works like a dropped image. Nothing is written to disk: "save this card" is the only way.

"Take a selfie and make it X" remembers X; it runs after Use Photo, or after the shot when nobody answers within AUTO_USE_S seconds (the orb uses the photo by itself)."""

from __future__ import annotations

import re
import time
from typing import Any

from julius import log
from julius.art import source

AUTO_USE_S = 5
PENDING_TTL_S = 600
_LEAD = r"^\s*(?:(?:please|hey julius|julius|okay|ok|now|then|and)[ ,]+)*(?:(?:can|could|would) you |i (?:want|need|would like) you to |i want to |i'd like to |let's |lets )?(?:please )?"
_TAKE = re.compile(
    _LEAD
    + r"(?:use (?:the |my )?(?:camera|webcam) to )?(?:take|snap|grab|shoot)\s+(?:me\s+)?(?:a|an|one|another|my|the)?\s*(?:new |quick |fresh )?(?:selfie|photo|picture|pic|portrait)\b(?!\s+of\s+(?!me\b|myself\b))",
    re.I,
)
_QUESTION = re.compile(
    r"^\s*(?:(?:hey julius|julius|okay|ok)[ ,]+)*(?:what|how|why|who|which|when|where|is|are|does|do you|explain|tell me)\b",
    re.I,
)
_THEN = re.compile(r"\b(?:and|then|so)\b\s*(.+)", re.I)

_pending: dict[str, Any] | None = (
    None  # {"then": text, "t": monotonic}: what to do with the next photo
)


def parse(text: str) -> dict | None:
    """{"then": the rest of the request or ""} when the user asks for a photo to be taken, else None. "Look at me" / "what am I holding" stay with the vision look."""
    t = " ".join((text or "").split())
    if not t or len(t) > 200 or _QUESTION.match(t):
        return None
    m = _TAKE.match(t)
    if not m:
        return None
    rest = t[m.end() :].strip(" .,!?")
    then = _THEN.match(rest)
    return {"then": then.group(1).strip() if then else ""}


def request(cfg: dict | None, panel: Any, then: str = "", autostart: bool = True) -> str:
    """Ask the orb to open its camera window; returns what Julius says. Never raises."""
    global _pending
    if (cfg or {}).get("camera", True) is False:
        return "The camera is switched off in my settings."
    if panel is None or not panel.camera_open(autostart, AUTO_USE_S if then else 0):
        return "The camera window needs the Julius orb app, and I can't reach it right now."
    _pending = {"then": then, "t": time.monotonic()} if then else None
    log.event("camera_requested", then=bool(then))
    return (
        "Opening the camera. Counting down from three, then I'll take it."
        if autostart
        else "Opening the camera."
    )


def take_pending(clock: Any = time.monotonic) -> str:
    """What was asked to happen after the photo ("make it pixel art"), once; "" when nothing, or when it has gone stale."""
    global _pending
    p, _pending = _pending, None
    if not p or clock() - p["t"] > PENDING_TTL_S:
        return ""
    return p["then"]


def cancel_pending() -> None:
    global _pending
    _pending = None


def _record_photo() -> None:
    from julius.brain import session_state

    session_state.record("photo", "took a camera photo (the current image)")


def received(
    jpeg: bytes, panel: Any = None, files: Any = None, turn: int | None = None, auto: bool = False
) -> str:
    """A photo came back from the orb's camera window: hold it in memory, show it as a still in the panel, return the follow-up request ("" when none)."""
    source.hold(jpeg)
    _record_photo()
    if panel is not None and files is not None:
        try:
            ref = files.put_memory(jpeg, "jpg")
            panel.camera(
                {
                    "title": "Photo",
                    "image": ref,
                    "note": "Held in memory only. Say save this card to keep it, or tell me what to make from it.",
                },
                turn=turn,
            )
            panel.set_camera_status(True)
        except Exception as exc:
            log.event("camera_card_failed", error=type(exc).__name__)
    log.event("camera_photo", bytes=len(jpeg), auto=auto)
    return take_pending()
