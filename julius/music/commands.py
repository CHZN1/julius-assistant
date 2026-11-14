"""Spoken sample-index commands that need no model: "rescan my samples", "update my sample index", "refresh my library". Deterministic, like the memory commands, so the tool description does not have to carry this wording."""

from __future__ import annotations

import re

RESCAN = re.compile(
    r"^\s*(?:(?:please|hey julius|julius|okay|ok)[ ,]+)*"
    r"(?:can you |could you |would you )?(?:please )?"
    r"(?:re-?scan|re-?index|refresh|update|reload)\s+"
    r"(?:my |the |all my )?"
    r"(?:samples?(?: (?:library|index|folders?))?|loops?(?: (?:library|index|folders?))?|"
    r"(?:sound|beat|audio) library|sample[- ]?index|library|index)"
    r"(?:\s+(?:please|now|again))?\s*[.!?]*\s*$",
    re.I,
)


def is_rescan(text: str) -> bool:
    return bool(RESCAN.match(text or ""))


def handle(text: str) -> str | None:
    """The reply when `text` asks to rescan the sample index, else None."""
    if not is_rescan(text):
        return None
    from julius.music import tool

    return tool.index_samples()
