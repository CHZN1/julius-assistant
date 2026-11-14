"""The `analyze_audio` tool: find the file the user named (or the one just dropped), measure BPM and key, show a card, return the facts for Julius to say."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from julius import config, log
from julius.music import analysis, policy

_drop_source: Callable[[], Any] | None = None
MAX_DEPTH = 4
MAX_VISITED = 20000
DEFAULT_FOLDERS = ["~/Downloads", "~/Documents", "~/Desktop"]


def set_drop_source(fn: Callable[[], Any] | None) -> None:
    """The service registers where dropped files are staged (a callable returning the pending drop, or None)."""
    global _drop_source
    _drop_source = fn


def search_folders() -> list[Path]:
    cfg = config.load_config().get("music") or {}
    names = cfg.get("search_folders") or DEFAULT_FOLDERS
    out = []
    for n in names:
        p = Path(os.path.expanduser(str(n)))
        if p.is_dir() and policy.denied(p) is None:
            out.append(p)
    return out


def _dropped() -> Path | None:
    pending = _drop_source() if _drop_source else None
    for item in (pending or {}).get("items", []):
        if item.get("kind") == "file":
            return Path(item["path"])
    return None


def find(name: str, roots: list[Path] | None = None) -> list[Path]:
    """Audio files whose name (or name without extension) matches, case-insensitively, under the search folders; protected folders are never entered."""
    want = name.strip().casefold()
    hits: list[Path] = []
    visited = 0
    for root in roots if roots is not None else search_folders():
        base = len(root.parts)
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and policy.denied(Path(dirpath) / d) is None
                and len(Path(dirpath).parts) - base < MAX_DEPTH
            ]
            for f in files:
                visited += 1
                p = Path(dirpath) / f
                if p.suffix.casefold() in policy.AUDIO_EXT and want in (
                    f.casefold(),
                    p.stem.casefold(),
                ):
                    hits.append(p)
            if visited > MAX_VISITED:
                return hits
    return hits


def resolve(path: str) -> Path:
    raw = (path or "").strip().strip("\"'")
    if not raw or raw.casefold() in (
        "dropped",
        "this",
        "this file",
        "the file",
        "that file",
        "dropped file",
    ):
        found = _dropped()
        if found is None:
            raise analysis.AudioProblem("Which file? Name it, or drop it on the orb first.")
        return found
    if raw.startswith(("/", "~")):
        return Path(os.path.expanduser(raw))
    hits = find(raw)
    if not hits:
        raise analysis.AudioProblem(
            f"I couldn't find an audio file called {raw} in Downloads, Documents or Desktop. Give me the full path or drop it on the orb."
        )
    if len(hits) > 1:
        shown = "; ".join(str(h.parent).replace(os.path.expanduser("~"), "~") for h in hits[:4])
        raise analysis.AudioProblem(
            f"AMBIGUOUS: {len(hits)} files are called {raw} ({shown}). Ask which folder."
        )
    return hits[0]


def _card(a: analysis.Analysis) -> bool:
    from julius.ui_bridge import charts

    if charts._panel is None:
        return False
    panel, _files, turn_fn = charts._panel
    spec = {
        "title": f"{a.name}",
        "unit": "",
        "columns": ["", "Result", "Confidence"],
        "rows": analysis.table_rows(a),
    }
    return panel.table(spec, turn=turn_fn()) is not None


def analyze_audio(path: str = "", **_) -> str:
    try:
        a = analysis.analyze(resolve(path))
    except analysis.AudioProblem as exc:
        return str(exc)
    except Exception as exc:
        log.event("audio_analysis_failed", error=type(exc).__name__)
        return "I couldn't analyse that file."
    shown = False
    try:
        shown = _card(a)
    except Exception as exc:
        log.event("audio_card_failed", error=type(exc).__name__)
    log.event("audio_analysed", bpm=a.bpm, key=a.key, seconds=a.analysed_seconds)
    return analysis.spoken(a) + (" Details are in the side panel." if shown else "")


def music_theory(
    key: str = "", progression: str = "", chord: str = "", scale: str = "", style: str = "", **_
) -> str:
    """Compute theory for a key, scale or mode, chord or chord progression; show the piano card; return the exact facts for Julius to explain."""
    from julius.music import styles, theory

    try:
        if str(style or "").strip():
            res = styles.styled(
                str(style), str(key or ""), str(progression or ""), str(chord or "")
            )
        else:
            res = theory.theory(
                str(key or ""), str(progression or ""), str(chord or ""), str(scale or "")
            )
    except theory.TheoryProblem as exc:
        return str(exc)
    except Exception as exc:
        log.event("theory_failed", error=type(exc).__name__)
        return "I couldn't work that out."
    shown = False
    try:
        from julius.ui_bridge import charts

        if charts._panel is not None:
            panel, _files, turn_fn = charts._panel
            shown = panel.theory(res.card, turn=turn_fn()) is not None
    except Exception as exc:
        log.event("theory_card_failed", error=type(exc).__name__)
    log.event("theory_answered", kind=res.card.get("kind"))
    return (
        res.facts
        + "\n\nEvery note name above was computed exactly: use only these, never add or change one from memory. Read each voicing exactly as given: when it says a note is doubled, say it that way (D F A, with D doubled) and never list that note twice. Explain briefly and musically (what each chord does, why it works, what to try); suggestions only, nothing is written anywhere."
        + (" The piano and chord chart are in the side panel." if shown else "")
    )


def index_samples(**_) -> str:
    """Refresh the index of the user's approved sample folders (names, sizes and dates only)."""
    from julius.music import library

    if not library.allowlist():
        return "No sample folders are approved for indexing yet. The folders are listed in julius.toml under [music] index_allowlist, and I only ever scan those."
    r = library.scan()
    return f"Indexed {r['files']} audio files in {r['folders']} approved folder(s); {r['removed']} no longer there. Names, sizes and dates only: nothing was opened, moved or changed."


def find_samples(query: str = "", bpm: int = 0, key: str = "", **_) -> str:
    """Search the user's own indexed samples by name, folder, BPM or key as written in the file names."""
    from julius.music import library

    if not library.allowlist():
        return "No sample folders are approved for indexing yet, so there is nothing to search."
    try:
        library.scan()  # cheap name-only refresh so new files show up
        hits = library.search(query, int(bpm) if bpm else None, key, limit=10)
    except Exception as exc:
        log.event("sample_index_failed", error=type(exc).__name__)
        return "I couldn't read the sample index."
    if not hits:
        return "Nothing in your indexed samples matches that."
    lines = [
        f"- {h['name']}{h['ext']} ({Path(h['folder']).name.strip()}"
        + (f", {h['bpm']} BPM in the name" if h["bpm"] else "")
        + (f", {h['key']} in the name" if h["key"] else "")
        + ")"
        for h in hits
    ]
    return (
        f"{len(hits)} match(es) from your own approved sample folders (BPM and key are only what the file names say; use analyze_audio to measure):\n"
        + "\n".join(lines)
    )
