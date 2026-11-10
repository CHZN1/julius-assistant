"""Spoken file names that the recognizer mangled: "TrashReport.TXT", "Reports.TXT", "Reports.Trash" when the user said "trash report.txt".

Used only AFTER an exact name found nothing, and only against the names that really exist in the one folder he named. A file is returned when it is the single plausible match;
the normal preview then names the real file and nothing happens without his yes. Several plausible files make him choose; none leaves the original "no matching files" answer."""

import difflib
import re
from typing import Any

_VERBS = ("trash", "delete", "remove", "bin")
_TOKEN = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9]{1,8}\b")


def _strip_verbs(word: str) -> str:
    w = word.lower()
    changed = True
    while changed:
        changed = False
        for v in _VERBS:
            if w.startswith(v) and len(w) > len(v) + 1:
                w = w[len(v) :]
                changed = True
    return w


def split(name: str) -> tuple[str, str]:
    """(stem key, extension) with the verb glue and a plural 's' removed from the stem: 'TrashReports' -> 'report'."""
    name = _strip_verbs(name.strip().strip("."))
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = re.sub(r"[^a-z0-9]", "", stem)
    if len(stem) > 3 and stem.endswith("s"):
        stem = stem[:-1]
    return stem, ext.lower()


def closest(
    pattern: str, spoken: str, names: list[str | Any], cutoff: float = 0.85
) -> list[str | Any]:
    """Names from `names` that plausibly are what was meant. `pattern` is what the router extracted; `spoken` is the whole transcript (its other file-looking words are candidates too)."""
    wants = {split(pattern)} | {split(t) for t in _TOKEN.findall(spoken or "")}
    wants = {w for w in wants if w[0]}
    exts = {n.rpartition(".")[2].lower() for n in names if "." in n}
    hits = []
    for name in names:
        s, e = split(name)
        for ws, we in wants:
            same_ext = (
                we == e or we not in exts
            )  # an extension that no file here has ("Reports.Trash") is recognition noise: match on the stem
            if same_ext and (ws == s or difflib.SequenceMatcher(None, ws, s).ratio() >= cutoff):
                hits.append(name)
                break
    return hits
