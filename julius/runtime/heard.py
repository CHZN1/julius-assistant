"""Mis-hearings and typos of the art words, fixed before routing. Rules, not a model: each fix only fires where the word stands in a command
("make me a ___", "open my ___") or where a palette is expected, so ordinary speech ("spite", "miss you") is left alone.

From the live test: "Open my sprayer." and "Open my, I'm gonna spray it." meant "open my sprite"; "Make me a Nest Brite." meant "make me an NES sprite";
after "the ones I can do are gameboy, pico8 and nes", the answers "Miss." and "Mess." meant "NES"; the typed "Make me a srpite" meant "sprite"."""

from __future__ import annotations

import re

# words Whisper is told to expect (the [asr] hint); saved sprite names are added at run time
ART_VOCABULARY = [
    "sprite",
    "sprites",
    "NES",
    "Pico 8",
    "pico8",
    "Game Boy",
    "gameboy",
    "chibi",
    "palette",
    "pixel art",
    "pixelate",
    "dither",
    "outline",
    "goatee",
    "afro",
    "mustache",
    "stubble",
    "beanie",
]

_SPRITE_SOUNDS = {
    "sprayer",
    "sprayers",
    "spray",
    "spray it",
    "spray right",
    "spright",
    "spryte",
    "sprit",
    "sprait",
    "spraite",
    "sprite's",
    "spritee",
}
_NES_SOUNDS = {"nes", "ness", "nest", "nez", "n e s", "n.e.s", "n.e.s.", "nes's", "nesse"}
_NES_ANSWER = _NES_SOUNDS | {
    "miss",
    "mess",
    "nas",
    "ms",
    "nis",
    "knees",
}  # only when a palette is the expected answer
_PICO = re.compile(r"\b(?:pico|peco|pekoe|pico\s?-)\s?(?:8|eight)\b", re.I)
_COMMAND = r"(?:make|open|reopen|save|edit|show|create|build|draw|give|turn|change)"
_FILLER = r"(?:\s*,?\s*(?:me|my|a|an|the|this|that|your|it|another|new|i'?m\s+gonna|um|uh|like)\b)*"
_SPRITE_SLOT = re.compile(
    rf"\b({_COMMAND}\b{_FILLER}[\s,]+)([a-z' .]{{3,14}}?)(?=[\s.!?,]*$|\s+(?:of|from|with|for|and|please)\b)",
    re.I,
)
_NES_SPRITE = re.compile(
    r"\b(?:nes|ness|nest|nez)\s*(?:brite|bright|sprite|spright|light|right|bite)\b", re.I
)


def _damerau(a: str, b: str) -> int:
    """Edit distance with transpositions ("srpite" -> "sprite" is 1)."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def _is_sprite(word: str) -> bool:
    w = re.sub(r"[^a-z' ]+", "", word.lower()).strip()
    if w in ("sprite", "sprites"):
        return False  # already right
    return w in _SPRITE_SOUNDS or (
        len(w) >= 5 and " " not in w and _damerau(w, "sprite") <= 1 and w not in ("spite", "spritz")
    )


def expects_palette(last_reply: str) -> bool:
    """Julius just named or asked for a palette ("the ones I can do are gameboy, pico8 and nes")."""
    t = (last_reply or "").lower()
    return "palette" in t or sum(w in t for w in ("gameboy", "game boy", "pico", "nes")) >= 2


def fix(text: str, last_reply: str = "", sprite_active: bool = False) -> str:
    """The words as meant. Returns the text unchanged when no rule fits."""
    t = str(text or "")
    if not t.strip():
        return t
    out = _NES_SPRITE.sub("NES sprite", t)
    out = _PICO.sub("pico8", out)
    bare = re.sub(r"[^a-z. ]+", "", out.lower()).strip(" .")
    if bare in _NES_ANSWER and (
        expects_palette(last_reply) or (sprite_active and bare in _NES_SOUNDS)
    ):
        return "NES"  # a one-word answer to "which palette?"
    if bare in ("sprite", "yes sprite", "yes, sprite") and sprite_active:
        return out

    def slot(m: re.Match) -> str:
        head, word = m.group(1), m.group(2)
        if _is_sprite(word):
            return (
                re.sub(r"(?:\s*,?\s*(?:i'?m\s+gonna|um|uh|like)\b)+", "", head, flags=re.I).rstrip(
                    " ,"
                )
                + " sprite"
            )
        w = word.lower().strip(" .")
        if w in _NES_SOUNDS and (sprite_active or expects_palette(last_reply)):
            return head + "NES"
        return m.group(0)

    out = _SPRITE_SLOT.sub(slot, out)
    out = re.sub(r"\b(a|an)\s+NES\b", "an NES", out)  # "make me a NES sprite" -> "an NES sprite"
    return out


def changed(before: str, after: str) -> bool:
    return before.strip() != after.strip()
