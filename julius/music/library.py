"""Index of the user's OWN sample folders and files: only the folders and individual files listed in `[music] index_allowlist` are ever scanned; nothing outside it, ever (a file entry approves that one file, not its neighbours).

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


VOLUMES = Path("/Volumes")


def _volume_root(p: Path) -> Path | None:
    """/Volumes/<name> for a path on an external drive, else None."""
    try:
        rel = p.relative_to(VOLUMES)
    except ValueError:
        return None
    return VOLUMES / rel.parts[0] if rel.parts else None


def entries() -> list[tuple[Path, str]]:
    """Every approved folder or file with its state: "online" (there and readable), "offline" (it is on an external drive that is not connected: kept, never dropped), or "blocked" (the drive is connected but macOS will not let this program read it). A missing entry that is not on an external drive is a typo or a deleted file and is dropped, as is anything the deny-list rejects."""
    names = (config.load_config().get("music") or {}).get("index_allowlist") or []
    out: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for n in names:
        p = Path(os.path.expanduser(str(n)))
        if policy.denied(p) is not None or str(p) in seen:
            continue
        vol = _volume_root(p)
        try:
            ok = p.is_dir() or (p.is_file() and p.suffix.casefold() in policy.AUDIO_EXT)
            state = "online" if ok else None
        except PermissionError:
            state = "blocked"
        if state is None and vol is not None and not vol.exists():
            state = "offline"
        if state:
            seen.add(str(p))
            out.append((p, state))
    return out


def allowlist() -> list[Path]:
    """The approved folders and single audio files that are readable right now."""
    return [p for p, st in entries() if st == "online"]


def _covering(p: Path, ents: list[tuple[Path, str]]) -> tuple[Path, str] | None:
    for root, st in ents:
        if p == root or root in p.parents:
            return root, st
        try:
            r, rr = p.resolve(), root.resolve()
        except OSError:
            continue
        if r == rr or rr in r.parents:
            return root, st
    return None


def volume_label(path: Path) -> str:
    vol = _volume_root(path)
    labels = (config.load_config().get("music") or {}).get("volume_labels") or {}
    if vol is None:
        return "an external drive"
    return str(labels.get(vol.name) or f"the drive {vol.name}")


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
    c.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v REAL)")
    return c


STALE_SECONDS = 600


def stale(db: Path | None = None) -> bool:
    """True when the index has never been scanned or the last scan is older than ten minutes (a large drive is not walked on every question)."""
    import time

    with _db(db) as c:
        r = c.execute("SELECT v FROM meta WHERE k='scanned'").fetchone()
    return r is None or time.time() - r["v"] > STALE_SECONDS


def scan(roots: list[Path] | None = None, db: Path | None = None) -> dict:
    """Index the allowlisted folders. `roots` can only narrow the allowlist; a folder outside it is refused."""
    allowed = allowlist()
    use = allowed if roots is None else [r for r in roots if _inside(r, allowed)]
    if not use:
        return {"folders": 0, "files": 0, "removed": 0, "refused": roots is not None}
    rows, seen = [], 0
    for root in use:
        if root.is_file():
            if root.is_symlink() or policy.denied(root) is not None:
                continue
            st = root.stat()
            bpm, key = parse_name(root.stem)
            rows.append(
                (
                    str(root),
                    root.stem,
                    str(root.parent),
                    root.suffix.casefold(),
                    st.st_size,
                    st.st_mtime,
                    bpm,
                    key,
                )
            )
            continue
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
    ents = entries()
    online = [(p, st) for p, st in ents if st == "online" and any(p == u for u in use)]
    with _db(db) as c:
        c.executemany("INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?,?)", rows)
        c.execute("INSERT OR REPLACE INTO meta VALUES ('scanned', ?)", (__import__("time").time(),))
        old = [r["path"] for r in c.execute("SELECT path FROM samples")]
        # only a file that was in a folder we just read and is no longer there is dropped; entries on a drive that is unplugged (or blocked) keep every row
        gone = [x for x in old if x not in keep and _covering(Path(x), online) is not None]
        c.executemany("DELETE FROM samples WHERE path=?", [(x,) for x in gone])
    return {"folders": len(use), "files": len(rows), "removed": len(gone), "refused": False}


def search(
    query: str = "",
    bpm: int | None = None,
    key: str = "",
    limit: int = 10,
    db: Path | None = None,
    match: str = "all",
) -> list[dict]:
    """Matches from the index, limited to what is inside the allowlist right now (an entry taken off the list stops showing at once). `match` "all" needs every filter given to match; "any" needs at least one (F minor OR about 180 BPM). Entries on a drive that is not connected are returned too, marked offline."""
    ents = entries()
    if not ents:
        return []
    words = [w for w in re.split(r"\W+", query.casefold()) if w]
    any_mode = str(match).casefold() in ("any", "or", "either")
    out = []
    with _db(db) as c:
        for r in c.execute("SELECT * FROM samples ORDER BY name"):
            d = dict(r)
            cov = _covering(Path(d["path"]), ents)
            if cov is None:
                continue
            tests = []
            if words:
                hay = (d["name"] + " " + Path(d["folder"]).name).casefold()
                tests.append(all(w in hay for w in words))
            if bpm is not None:
                tests.append(d["bpm"] is not None and abs(d["bpm"] - bpm) <= 3)
            if key:
                tests.append((d["key"] or "").casefold() == _norm_key(key))
            if tests and not (any(tests) if any_mode else all(tests)):
                continue
            d["state"] = cov[1]
            d["offline"] = cov[1] != "online"
            if d["offline"]:
                d["where"] = volume_label(Path(d["path"]))
            out.append(d)
    return group(out)[: max(1, min(limit, 25))]


def group(rows: list[dict]) -> list[dict]:
    """One entry per sound, however many formats it exists in: CHZN1_Mercy.mp3 and CHZN1_Mercy.wav are shown once with both formats."""
    by: dict[tuple[str, str], dict] = {}
    for d in rows:
        k = (d["folder"], d["name"].casefold())
        if k not in by:
            by[k] = {**d, "formats": [d["ext"]], "paths": [d["path"]]}
        else:
            by[k]["formats"].append(d["ext"])
            by[k]["paths"].append(d["path"])
            by[k]["bpm"] = by[k]["bpm"] or d["bpm"]
            by[k]["key"] = by[k]["key"] or d["key"]
    for e in by.values():
        e["formats"] = sorted(set(e["formats"]))
    return list(by.values())


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
