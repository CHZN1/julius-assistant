"""What Julius has done this session, told to the brain every turn, so it never denies its own actions and never asks for a picture it already has.

The live failure: after making two sprites from a camera photo, "make me an NES sprite" got "drop a photo on the orb", and "you already made two" got
"I haven't made any sprites yet". The brain only saw the last few messages, not what the tools did. Now each turn's request carries a short, factual
block: the current image, the current sprite, and the recent actions (newest last). It is data, never an instruction from the user.

The same block carries the one-question rule: when Julius asked a clarifying question last turn, the next turn acts on the most likely reading."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

MAX_ACTIONS = 12
_actions: deque = deque(maxlen=MAX_ACTIONS)
_counts: dict[str, int] = {}


def record(kind: str, text: str, clock: Callable[[], float] = time.time) -> None:
    """One thing Julius did ("made a sprite", "saved Sunday Roy"), for the brain's context."""
    _actions.append((clock(), kind, text))
    _counts[kind] = _counts.get(kind, 0) + 1


def reset() -> None:
    _actions.clear()
    _counts.clear()


def actions() -> list[tuple[float, str, str]]:
    return list(_actions)


def count(kind: str) -> int:
    return _counts.get(kind, 0)


def _when(t: float) -> str:
    return time.strftime("%H:%M", time.localtime(t))


def block(last_reply: str = "") -> str:
    """The session-state lines for this turn, or "" when nothing happened yet."""
    from julius.art import source, sprite_doc

    lines = []
    cur = source.current()
    if cur:
        what = "camera selfie" if cur.get("kind") == "camera" else f"file {cur.get('label', '')}"
        lines.append(
            f"Current image: {what} ({_when(cur['at'])}); 'this', 'my photo' and new sprites use it (source 'dropped') without asking for a picture."
        )
    doc = sprite_doc.STUDIO.get()
    if doc is not None:
        name = doc.get("name") or (f"sprite of {doc['label']}" if doc.get("label") else "unnamed")
        lines.append(
            f"Current sprite: {name}, {doc['palette']} {doc['head']} px; edits go to it with sprite_edit."
        )
    made = [(t, text) for t, k, text in _actions if k == "sprite"]
    if count("sprite"):
        detail = "; ".join(
            f"{_when(t)} {text.removeprefix('made a sprite ')}" for t, text in made[-5:]
        )
        lines.insert(
            0,
            f"Julius made {count('sprite')} sprite{'s' if count('sprite') != 1 else ''} this session ({detail}). If he says so, confirm it.",
        )
    if _actions:
        lines.append(
            "Recent actions (newest last): "
            + "; ".join(f"{_when(t)} {text}" for t, _k, text in list(_actions)[-8:])
            + "."
        )
    if lines:
        from julius.art import tool as art_tool

        main = art_tool.main_sprite()
        if main:
            lines.append(
                f"Main sprite: {main[0]} (body, clothing and animation start from it unless he names another; 'open my main sprite' opens it)."
            )
    if "?" in last_reply:
        lines.append(
            "You asked a clarifying question last turn: do not ask another. Act on the most likely reading (toward the current image or sprite) and say how to undo it."
        )
    if not lines:
        return ""
    return (
        "SESSION STATE (true, from Julius's own records; never deny or contradict it, and never ask for something listed here):\n"
        + "\n".join("- " + x for x in lines)
    )


def reminder(last_reply: str = "") -> str:
    """A one-line, second-person version of the record that rides with the user's words. Haiku ignored the full block in the system prompt for
    "you already made two" (0 of 4 confirmed) and followed this line (4 of 4)."""
    from julius.art import source, sprite_doc

    bits = []
    n = count("sprite")
    cur = source.current()
    if n:
        bits.append(f"you made {n} sprite{'s' if n != 1 else ''} this session")
    if cur:
        what = (
            "the camera selfie"
            if cur.get("kind") == "camera"
            else f"the file {cur.get('label', '')}"
        )
        bits.append(f"{what} is the current image (use source 'dropped'; never ask for a photo)")
    doc = sprite_doc.STUDIO.get()
    if doc is not None:
        bits.append(
            f"the current sprite is {doc.get('name') or 'the newest one'} ({doc['palette']}, {doc['head']} px)"
        )
    if "?" in (last_reply or "") and bits:
        bits.append(
            "you already asked one question: act on the most likely reading now and say how to undo"
        )
    if not bits:
        return ""
    return "[Julius's own record, true: " + "; ".join(bits) + ".]"
