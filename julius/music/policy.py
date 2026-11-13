"""Where music tools must never look: Logic, GarageBand, system audio, plug-in and sample-library folders. One rule for analysis, the sample index and the batch tools."""

from __future__ import annotations

import os
import re
from pathlib import Path

AUDIO_EXT = frozenset(
    {".wav", ".wave", ".aif", ".aiff", ".aifc", ".mp3", ".flac", ".m4a", ".caf", ".ogg"}
)

# Folder or file names (whole words) that are never read, indexed, converted or renamed.
_DENIED_NAME = re.compile(
    r"(?<![a-z])(?:logic|garage[ _-]?band|waves|native[ _-]?instruments|kontakt|komplete|maschine|slate|zebra|spitfire|ni|"
    r"plug[ _-]?ins?|audio[ _-]?plug[ _-]?ins?|apple[ _-]?loops|audio[ _-]?music[ _-]?apps)(?![a-z])",
    re.I,
)
_DENIED_SUFFIX = (
    ".logicx",
    ".band",
    ".component",
    ".vst",
    ".vst3",
    ".aaxplugin",
    ".app",
    ".bundle",
    ".plugin",
    ".nks",
    ".nkx",
    ".nkc",
    ".ncw",
)
_DENIED_ROOTS = (
    "/Library/Audio",
    "/System",
    "/Library/Application Support/Logic",
    "/Library/Application Support/GarageBand",
    "/Applications",
)


def _parts_reason(p: Path) -> str | None:
    s = str(p)
    for root in _DENIED_ROOTS + (
        os.path.expanduser("~/Library/Audio"),
        os.path.expanduser("~/Library/Application Support/Logic"),
        os.path.expanduser("~/Library/Application Support/GarageBand"),
    ):
        if s == root or s.startswith(root + "/"):
            return "that is a protected music-software location"
    for part in p.parts:
        if part.casefold().endswith(_DENIED_SUFFIX):
            return "that is inside a project, plug-in or app bundle"
        if _DENIED_NAME.search(part):
            return (
                "that is a Logic, GarageBand, plug-in or sample-library folder, which I never touch"
            )
    return None


def denied(path: str | Path) -> str | None:
    """Why this path is off limits (checked on the path as given AND after following links), or None."""
    p = Path(os.path.expanduser(str(path)))
    try:
        absolute = Path(os.path.abspath(p))
        real = p.resolve()
    except (OSError, RuntimeError):
        return "the path could not be checked"
    return _parts_reason(absolute) or _parts_reason(real)
