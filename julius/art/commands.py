"""Spoken art commands that need no model: "turn this photo I just dropped into pixel art", "make sunset.jpg pixel art, 32 pixels, gameboy palette, outline, 8x". Deterministic, like the rescan and convert commands: the brain's tool list stays as it is, and a dropped image never has to be guessed at.

Julius does not draw or generate pictures. "Make me a pixel art dragon" is answered with what it can do instead."""

from __future__ import annotations

import re

_LEAD = r"^\s*(?:(?:please|hey julius|julius|okay|ok|now|then|and)[ ,]+)*(?:(?:can|could|would) you |i (?:want|need|would like) you to |i want to |i'd like to )?(?:please )?"
_VERB = r"(?:make|turn|convert|change|create|render|process|run|give|do|redo|show|compare|pixelate|pixelize|pixelise|pixel[- ]?art)\b"
_ART = re.compile(
    r"pixel[- ]?art|pixel(?:ate|ize|ise)d?\b|\b(?:8|16)[- ]?bit (?:style|art|look|graphics|version)",
    re.I,
)
_A_SPRITE = re.compile(r"\b(?:a|my|the|me)\s+(?:game\s+|retro\s+)?sprite\b", re.I)
_QUESTION = re.compile(
    r"^\s*(?:(?:hey julius|julius|okay|ok)[ ,]+)*(?:what|what's|whats|how|why|who|which|when|where|is|are|does|do you|explain|tell me|define)\b",
    re.I,
)
_FILE = re.compile(r"([^\s\"'<>|]+\.(?:png|jpe?g|webp|gif|bmp|tiff?))(?![A-Za-z0-9])", re.I)
_HERE = re.compile(
    r"\b(?:this|that|the|my|these|those)\s+(?:(?:just )?dropped |new |latest |last |same )?(?:photo|picture|pic|image|selfie|screenshot|drawing|logo|file)s?\b|\bjust dropped\b|\bdropped (?:photo|picture|pic|image|file)\b|\bit\b",
    re.I,
)
_GENERATE = re.compile(
    r"\b(?:make|draw|create|generate|give|paint|design)\s+(?:me\s+)?(?:a|an|some|the)\s+(?:\w+\s+){0,3}?pixel",
    re.I,
)


def parse(text: str) -> dict | None:
    """{"source": file name or "dropped", "words": the whole request} for a pixel-art request, {"generate": True} when it asks Julius to draw one from nothing, else None."""
    t = (text or "").strip()
    if not t or _QUESTION.match(t) or not _ART.search(t):
        return None
    if _A_SPRITE.search(
        t
    ):  # "make me a sprite and compare it with pixel art" is a sprite (the brain), not plain pixel art
        return None
    if not re.match(_LEAD + _VERB, t, re.I) and not re.match(
        _LEAD + r"(?:pixelate|pixelize|pixelise)\b", t, re.I
    ):
        return None
    f = _FILE.search(t)
    if f:
        return {"source": f.group(1), "words": t}
    if _GENERATE.search(t) and not _HERE.search(t.split("pixel")[0] if "pixel" in t.lower() else t):
        return {"generate": True, "words": t}
    return {"source": "dropped", "words": t}


OPEN_SPRITE = re.compile(
    r"^\W*(?:please\s+|can you\s+|could you\s+)?(?:re)?open\s+(?:up\s+)?(?:my\s+|the\s+)?(?:(?:saved|last|latest|newest)\s+)?(?:sprite\b\s*(?:called\s+|named\s+)?)?(.{0,60}?)[\s.!?]*$",
    re.I,
)
SPRITE_BUTTON = re.compile(r"^\s*sprite\s*#([0-9a-f]{4,16})\s*:\s*(.{1,120})$", re.I)


def handle(text: str) -> str | None:
    """The reply when `text` asks for pixel art, or is a sprite card button ("sprite #ab12cd34: bigger beard"), else None."""
    m = SPRITE_BUTTON.match(text or "")
    if m:  # the card's edit buttons: deterministic, no model call
        from julius.art import tool

        return tool.sprite_edit(m.group(2), m.group(1).lower())
    m = OPEN_SPRITE.match(text or "")
    if m and (
        re.search(r"\bsprite\b", text, re.I) or (m.group(1).strip() and _sprite_named(m.group(1)))
    ):
        from julius.art import (
            tool,
        )  # "open my sprite", "open Sunday Roy" (a saved sprite's name): deterministic, no model call

        return tool.sprite_edit("open " + m.group(1))
    req = parse(text)
    if req is None:
        return None
    if req.get("generate"):
        return (
            "I don't draw or generate pictures, that stays yours. I can turn a photo or picture of yours into pixel art: "
            "drop it on the orb, or name a file in one of your art folders, and say how big and how many colours."
        )
    from julius.art import tool

    return tool.pixel_art(req["source"], **tool.parse_options(req["words"]))


def _sprite_named(name: str) -> bool:
    """True when a saved sprite has exactly this name ("open Sunday Roy"); anything else is not a sprite request."""
    from julius.art import tool

    words = tool._words(name)
    return bool(words) and any(r[0].lower() == " ".join(words) for r in tool.saved_sprites())


PALETTE_WORD = re.compile(
    r"^\W*(?:(?:the|an?|use|in|with|make it|do)\s+)*(nes|pico\s?-?8|pico\s?eight|game\s?boy|gb)(?:\s+(?:palette|one|please|sprite))?\W*$",
    re.I,
)


def palette_answer(text: str, last_reply: str, active: bool) -> str | None:
    """A palette name on its own ("NES", "pico8", "game boy") while a sprite or picture is current, or right after a palette question: that
    sprite from the current picture, no model call and no "drop a photo" (the live test asked for a photo it already had)."""
    from julius.runtime import heard

    m = PALETTE_WORD.match(text or "")
    if not m or not (active or heard.expects_palette(last_reply)):
        return None
    word = re.sub(r"[\s-]", "", m.group(1).lower())
    palette = "nes" if word == "nes" else "pico8" if word.startswith("pico") else "gameboy"
    from julius.art import tool

    return tool.image_style("dropped", style="sprite", palette=palette)


_YES_OPEN = re.compile(
    r"^\W*(?:yes|yeah|yep|yup|sure|right|ok(?:ay)?)?[\s,.!]*(?:(?:my|the|that|the newest|the latest|the last)\s+)?(?:sprite|one|newest(?:\s+one)?|latest(?:\s+one)?)?[\s.!]*$",
    re.I,
)


def answer_to_open(text: str, last_reply: str) -> str | None:
    """ "Yes, sprite." / "the newest one" right after Julius asked which sprite to open: open it (one question at most, then act)."""
    t = (last_reply or "").lower()
    if not ("?" in t and "open" in t and "sprite" in t):
        return None
    if not (text or "").strip() or not _YES_OPEN.match(text):
        return None
    from julius.art import tool

    return tool.sprite_edit("open my sprite")
