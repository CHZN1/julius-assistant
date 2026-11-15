"""Take a photo with the camera, for art: "take a photo", "take a selfie", "take a selfie and make it pixel art".

A 3-second countdown with a live (mirrored) preview in the side panel, then one full frame. The helper (visiond `live`) keeps the camera session open the whole time,
so the macOS camera light is on while the preview runs and while the photo is taken, and goes off when the helper exits. Preview frames and the photo travel over the helper's
stdout into memory; nothing is written to disk. The photo is held in memory (see `source.hold`) and then works like a dropped image. "Save this card" is the only way it is saved."""

from __future__ import annotations

import base64
import re
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from julius import log
from julius.art import source

COUNTDOWN_S = 3
_LEAD = r"^\s*(?:(?:please|hey julius|julius|okay|ok|now|then|and)[ ,]+)*(?:(?:can|could|would) you |i (?:want|need|would like) you to |i want to |i'd like to |let's |lets )?(?:please )?"
_TAKE = re.compile(
    _LEAD
    + r"(?:take|snap|grab|shoot)\s+(?:me\s+)?(?:a|an|one|another|my|the)?\s*(?:new |quick |fresh )?(?:selfie|photo|picture|pic|portrait)\b(?!\s+of\s+(?!me\b|myself\b))",
    re.I,
)
_QUESTION = re.compile(
    r"^\s*(?:(?:hey julius|julius|okay|ok)[ ,]+)*(?:what|how|why|who|which|when|where|is|are|does|do you|explain|tell me)\b",
    re.I,
)
_THEN = re.compile(r"\b(?:and|then|so)\b.+", re.I)


class CameraProblem(Exception):
    """Something Julius says instead of taking the photo."""


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
    return {"then": then.group(0) if then else ""}


def _why(line: str) -> str:
    if "permission" in line:
        return "I need camera permission. You can allow it in System Settings, under Privacy and Security, then Camera."
    if "busy" in line or "no-camera" in line:
        return "I can't use the camera right now."
    return "I couldn't take a photo."


def stream(
    seconds: float,
    on_frame: Callable[[bytes], None],
    popen: Callable = subprocess.Popen,
    binary: Path | None = None,
) -> bytes:
    """Run the helper's live mode: calls on_frame with each preview JPEG and returns the final photo JPEG. Raises CameraProblem."""
    from julius.agents import vision

    try:
        proc = popen(
            [str(binary or vision.build()), "live", str(seconds)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, vision.VisionProblem):
        raise CameraProblem("I couldn't take a photo.") from None
    timer = threading.Timer(seconds + 25, proc.kill)  # a stuck helper must not keep the camera on
    timer.start()
    photo, last = None, ""
    try:
        for raw in proc.stdout:
            line = raw.strip()
            kind, _, rest = line.partition(" ")
            if kind == "frame":
                try:
                    on_frame(base64.b64decode(rest))
                except Exception as exc:  # a preview problem must not lose the photo
                    log.event("camera_preview_failed", error=type(exc).__name__)
            elif kind == "photo":
                photo = base64.b64decode(rest)
            else:
                last = line
        proc.wait(timeout=10)
    except (OSError, ValueError, subprocess.SubprocessError):
        proc.kill()
        raise CameraProblem("I couldn't take a photo.") from None
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
    if photo is None or not last.startswith("ok"):
        raise CameraProblem(_why(last))
    return photo


def take(
    cfg: dict | None = None,
    panel: Any = None,
    files: Any = None,
    turn: int | None = None,
    popen: Callable = subprocess.Popen,
    binary: Path | None = None,
    seconds: float = COUNTDOWN_S,
) -> str:
    """Countdown with a live preview, capture, hold the photo in memory. Returns what Julius says (problems included, never raises)."""
    if (cfg or {}).get("camera", True) is False:
        return "The camera is switched off in my settings."
    card = None
    state = {"count": int(seconds), "t0": None}
    shown = panel is not None and files is not None
    if shown:
        card = panel.camera(
            {
                "title": "Say cheese",
                "phase": "countdown",
                "count": int(seconds),
                "note": "The camera light is on.",
            },
            turn=turn,
        )

    import time

    def on_frame(jpeg: bytes) -> None:
        if not shown or card is None:
            return
        now = time.monotonic()
        if state["t0"] is None:
            state["t0"] = now
        left = max(0, int(seconds - (now - state["t0"]) + 0.999))
        panel.camera_update(
            card,
            image=files.put_live(jpeg),
            count=left,
            phase="countdown" if left > 0 else "shooting",
        )

    try:
        photo = stream(seconds, on_frame, popen, binary)
    except CameraProblem as e:
        if card:
            panel.camera_update(card, phase="failed", count=0, summary=str(e))
        return str(e)
    source.hold(photo)
    if card:
        ref = files.put_memory(photo, "jpg", keep=16)
        panel.camera_update(
            card,
            phase="done",
            count=0,
            image=ref,
            summary="Held in memory only. Say save this card to keep it.",
        )
    log.event("camera_photo", bytes=len(photo))
    return "Got it. The photo is held in memory only; say save this card to keep it, or tell me what to make from it."
