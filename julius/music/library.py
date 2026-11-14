"""Index of the user's OWN sample folders: only folders listed in `[music] index_allowlist` are ever scanned; nothing outside it, ever.

Read-only: file names, sizes and dates are read, never audio content, and nothing is renamed, moved, written or deleted in those folders. The deny-list (policy.py) is enforced on top of the allowlist. The index itself is one small SQLite file under ~/.julius/. A BPM or key is only what the file name says (CHZN1_Cake_BPM160.wav); measure the real one with analyze_audio."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from julius import config
from julius.music import policy

DB_PATH = Path.home() / ".julius" / "sample_index.sqlite3"
MAX_DEPTH = 6
MAX_FILES = 20000
_BPM = re.compile(r"(?<![a-z])(?:bpm[ _-]?(\d{2,3})|(\d{2,3})[ _-]?bpm)(?![a-z0-9])", re.I)
_KEY = re.compile(r"(?<![A-Za-z])([A-G][#b]?)[ _-]?(maj(?:or)?|min(?:or)?|m)(?![a-z])")


def allowlist() -> list[Path]:
    """The folders the user approved, as they exist now. No setting means no folders, and a folder the deny-list rejects is dropped."""
    names = (config.load_config().get("music") or {}).get("index_allowlist") or []
    out: list[Path] = []
    for n in names:
        p = Path(os.path.expanduser(str(n)))
        if (
            p.is_dir()
            and policy.denied(p) is None
            and p.resolve() not in [q.resolve() for q in out]
        ):
            out.append(p)
    return out


def _inside(p: Path, roots: list[Path]) -> bool:
    try:
        r = p.resolve()
    except OSError:
        return False
    return any(r == root.resolve() or root.resolve() in r.parents for root in roots)


def parse_name(stem: str) -> tuple[int | None, str | None]:
    """(bpm, key) as written in the file name, or None."""
    m = _BPM.search(stem)
    bpm = int(next(g for g in m.groups() if g)) if m else None
    if bpm is not None and not 40 <= bpm <= 300:
        bpm = None
    k = _KEY.search(stem)
    key = None
    if k:
        minor = k.group(2).casefold().startswith("m") and not k.group(2).casefold().startswith(
            "maj"
        )
        key = k.group(1) + (" minor" if minor else " major")
    return bpm, key


def _db(path: Path | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE IF NOT EXISTS samples(path TEXT PRIMARY KEY, name TEXT, folder TEXT, ext TEXT, size INTEGER, mtime REAL, bpm INTEGER, key TEXT)"
    )
    return c


def scan(roots: list[Path] | None = None, db: Path | None = None) -> dict:
    """Index the allowlisted folders. `roots` can only narrow the allowlist; a folder outside it is refused."""
    allowed = allowlist()
    use = allowed if roots is None else [r for r in roots if _inside(r, allowed)]
    if not use:
        return {"folders": 0, "files": 0, "removed": 0, "refused": roots is not None}
    rows, seen = [], 0
    for root in use:
        base = len(root.parts)
        for dirpath, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [
                d
                for d in dirs
                if not d.startswith(".")
                and policy.denied(Path(dirpath) / d) is None
                and len(Path(dirpath).parts) - base < MAX_DEPTH
            ]
            for f in sorted(files):
                p = Path(dirpath) / f
                if f.startswith(".") or p.suffix.casefold() not in policy.AUDIO_EXT:
                    continue
                if p.is_symlink() or not _inside(p, allowed) or policy.denied(p) is not None:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                bpm, key = parse_name(p.stem)
                rows.append(
                    (
                        str(p),
                        p.stem,
                        str(root),
                        p.suffix.casefold(),
                        st.st_size,
                        st.st_mtime,
                        bpm,
                        key,
                    )
                )
                seen += 1
                if seen >= MAX_FILES:
                    break
    keep = {r[0] for r in rows}
    with _db(db) as c:
        c.executemany("INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?)", rows)
        old = [r["path"] for r in c.execute("SELECT path FROM samples")]
        gone = [x for x in old if x not in keep]
        c.executemany("DELETE FROM samples WHERE path=?", [(x,) for x in gone])
    return {"folders": len(use), "files": len(rows), "removed": len(gone), "refused": False}


def search(
    query: str = "",
    bpm: int | None = None,
    key: str = "",
    limit: int = 10,
    db: Path | None = None,
) -> list[dict]:
    """Matches from the index, limited to what is inside the allowlist right now (a folder taken off the list stops showing at once)."""
    allowed = allowlist()
    if not allowed:
        return []
    words = [w for w in re.split(r"\W+", query.casefold()) if w]
    out = []
    with _db(db) as c:
        for r in c.execute("SELECT * FROM samples ORDER BY name"):
            d = dict(r)
            if not _inside(Path(d["path"]), allowed):
                continue
            hay = (d["name"] + " " + Path(d["folder"]).name).casefold()
            if words and not all(w in hay for w in words):
                continue
            if bpm is not None and (d["bpm"] is None or abs(d["bpm"] - bpm) > 3):
                continue
            if key and (d["key"] or "").casefold() != _norm_key(key):
                continue
            out.append(d)
    return out[: max(1, min(limit, 25))]


def _norm_key(k: str) -> str:
    m = _KEY.search(k.strip().replace(" ", "")) or re.match(
        r"([A-Ga-g][#b]?)\s*(major|minor|maj|min|m)?$", k.strip()
    )
    if not m:
        return k.casefold()
    root = m.group(1)[0].upper() + m.group(1)[1:]
    q = (m.group(2) or "major").casefold()
    return (
        f"{root} {'minor' if q.startswith('m') and not q.startswith('maj') else 'major'}".casefold()
    )
