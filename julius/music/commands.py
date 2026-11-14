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


def convert_reply(s, text: str) -> str | None:
    """ "convert kick.wav to mp3", "convert all the wav files in the workspace to aiff 48k 24 bit": a convert PLAN through the file agent (preview, then yes or cancel, then undo). Copies go into the audio workspace only."""
    from julius.music import convert as cv

    req = cv.parse(text)
    if req is None:
        return None
    if req["error"]:
        return req["error"]
    spec = req["spec"]
    fs = s.file_session
    agent = fs.agent
    ws = cv.workspace()
    kind, pattern = cv.selection(req["what"])
    source = ws
    if kind == "named":
        if "." not in pattern:
            pattern += ".*"
        if not list(ws.glob(pattern)):
            for home in ("Documents", "Desktop", "Downloads"):
                folder = agent.root / home
                if folder.is_dir() and list(folder.glob(pattern)):
                    source = folder
                    break
    out = agent.propose(
        "convert",
        source=str(source),
        destination=str(ws / "converted" / spec.folder),
        pattern=pattern,
        convert=spec.as_dict(),
        turn=s.turn,
    )
    fs._replace_pending()
    fs._remember_plan(out)
    return out["message"]
