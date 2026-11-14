"""Index of the user's OWN sample folders and files: only the folders and individual files listed in `[music] index_allowlist` are ever scanned; nothing outside it, ever (a file entry approves that one file, not its neighbours).

Read-only: file names, sizes and dates are read, never audio content, and nothing is renamed, moved, written or deleted in those folders. The deny-list (policy.py) is enforced on top of the allowlist. The index itself is one small SQLite file under ~/.julius/. A BPM or key is only what the file name says (CHZN1_Cake_BPM160.wav); measure the real one with analyze_audio."""

from __future__ import annotations

import fnmatch
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
_UUID_CACHE: dict[str, tuple[float, str | None]] = {}
_VOL_PREFIX = "volume:"


def volume_uuid(mount: Path) -> str | None:
    """The volume UUID of a mounted drive (from diskutil, which needs no file access), or None."""
    import plistlib
    import subprocess
    import time

    key = str(mount)
    hit = _UUID_CACHE.get(key)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    uuid = None
    try:
        out = subprocess.run(
            ["diskutil", "info", "-plist", key], capture_output=True, timeout=10, check=True
        ).stdout
        uuid = (plistlib.loads(out).get("VolumeUUID") or "").upper() or None
    except Exception:
        uuid = None
    _UUID_CACHE[key] = (time.time(), uuid)
    return uuid


def mounts() -> dict[str, Path]:
    """UUID -> mount point for the drives connected now. A renamed mount (CHZN1 vs "CHZN1 1") is the same UUID."""
    out: dict[str, Path] = {}
    try:
        kids = sorted(VOLUMES.iterdir())
    except OSError:
        return out
    for k in kids:
        if k.is_symlink() or not k.is_dir():
            continue
        u = volume_uuid(k)
        if u:
            out[u] = k
    return out


def _canon(p: Path) -> str:
    """The stable name of a path: files on a drive are "volume:<UUID>/relative/path" whatever the mount is called today."""
    if str(p).startswith(_VOL_PREFIX):
        return str(p)
    vol = _volume_root(p)
    if vol is not None:
        for u, m in mounts().items():
            if m == vol:
                rel = p.relative_to(vol)
                return f"{_VOL_PREFIX}{u}" + (f"/{rel}" if str(rel) != "." else "")
    return str(p)


def _volume_root(p: Path) -> Path | None:
    """/Volumes/<name> for a path on an external drive, else None."""
    try:
        rel = p.relative_to(VOLUMES)
    except ValueError:
        return None
    return VOLUMES / rel.parts[0] if rel.parts else None


def _uuid_of(canon: str) -> str | None:
    if canon.startswith(_VOL_PREFIX):
        return canon[len(_VOL_PREFIX) :].split("/", 1)[0].upper()
    return None


def entries() -> list[tuple[Path, str]]:
    """Every approved folder or file with its state: "online" (there and readable), "offline" (it is on an external drive that is not connected: kept, never dropped), or "blocked" (the drive is connected but macOS will not let this program read it). A drive is named by its volume UUID ("volume:<UUID>/Beats"), so a changed mount name does not matter; plain /Volumes paths still work. A missing entry that is not on an external drive is a typo or a deleted file and is dropped, as is anything the deny-list rejects."""
    names = (config.load_config().get("music") or {}).get("index_allowlist") or []
    out: list[tuple[Path, str]] = []
    seen: set[str] = set()
    live = None
    for n in names:
        raw = os.path.expanduser(str(n))
        uuid = _uuid_of(raw)
        if uuid is not None:
            live = mounts() if live is None else live
            rel = raw[len(_VOL_PREFIX) :].split("/", 1)[1] if "/" in raw[len(_VOL_PREFIX) :] else ""
            mount = live.get(uuid)
            p = (mount / rel) if mount is not None else Path(raw)
            vol = None if mount is not None else p
        else:
            p = Path(raw)
            vol = _volume_root(p)
        if policy.denied(p) is not None or _canon(p) in seen:
            continue
        state = None
        if uuid is not None and mount is None:
            state = "offline"
        else:
            try:
                ok = (
                    p.parent.is_dir()
                    if is_glob(p)
                    else p.is_dir() or (p.is_file() and p.suffix.casefold() in policy.AUDIO_EXT)
                )
                state = "online" if ok else None
                if ok and _canon(p).startswith(_VOL_PREFIX):
                    # a stat can succeed where macOS still refuses to list the folder
                    with os.scandir(p.parent if (is_glob(p) or p.is_file()) else p) as it:
                        next(it, None)
            except PermissionError:
                state = "blocked"
            if state is None and uuid is None and vol is not None and not vol.exists():
                state = "offline"
        if state:
            seen.add(_canon(p))
            out.append((p, state))
    return out


def allowlist() -> list[Path]:
    """The approved folders and single audio files that are readable right now."""
    return [p for p, st in entries() if st == "online"]


def _covering(p: Path, ents: list[tuple[Path, str]]) -> tuple[Path, str] | None:
    ps = str(p)
    for root, st in ents:
        if is_glob(root):
            rc = _canon(root)
            parent, _, pat = rc.rpartition("/")
            pp, _, name = ps.rpartition("/")
            if pp == parent and fnmatch.fnmatch(name.casefold(), pat.casefold()):
                return root, st
            continue
        rs = _canon(root).rstrip("/")
        if ps == rs or ps.startswith(rs + "/"):
            return root, st
    for root, st in ents:
        if str(root).startswith(_VOL_PREFIX):
            continue
        try:
            r, rr = p.resolve(), root.resolve()
        except OSError:
            continue
        if r == rr or rr in r.parents:
            return root, st
    return None


def volume_label(path: Path) -> str:
    """ "your CHZN drive" for a labelled drive (`[music] volume_labels`, keyed by UUID or mount name), else "the drive <name>"."""
    labels = (config.load_config().get("music") or {}).get("volume_labels") or {}
    c = _canon(path)
    uuid = _uuid_of(c)
    if uuid is not None:
        name = next((m.name for u, m in mounts().items() if u == uuid), None)
        return str(labels.get(uuid) or (name and labels.get(name)) or f"the drive {name or uuid}")
    vol = _volume_root(path)
    if vol is None:
        return "an external drive"
    return str(labels.get(vol.name) or f"the drive {vol.name}")


def is_glob(p: Path) -> bool:
    """A pattern entry ("<folder>/CHZN1*"): the files of that one folder whose names match, case-insensitively, never its subfolders."""
    return any(ch in p.name for ch in "*?[")


def _matches_root(p: Path, root: Path) -> bool:
    if is_glob(root):
        try:
            return p.parent.resolve() == root.parent.resolve() and fnmatch.fnmatch(
                p.name.casefold(), root.name.casefold()
            )
        except OSError:
            return False
    try:
        r, rr = p.resolve(), root.resolve()
    except OSError:
        return False
    return r == rr or rr in r.parents


def _inside(p: Path, roots: list[Path]) -> bool:
    return any(_matches_root(p, root) for root in roots)


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


def scan(
    roots: list[Path] | None = None, db: Path | None = None, include_drives: bool = False
) -> dict:
    """Index the allowlisted folders. `roots` can only narrow the allowlist; a folder outside it is refused. Entries on an external drive are walked only when asked for (include_drives, "rescan my samples") or when the drive has just been connected, never on a plain search."""
    allowed = allowlist()
    use = allowed if roots is None else [r for r in roots if _inside(r, allowed)]
    if not include_drives:
        use = [r for r in use if not _canon(r).startswith(_VOL_PREFIX)]
    if not use:
        return {"folders": 0, "files": 0, "removed": 0, "refused": roots is not None}
    rows, seen = [], 0
    for root in use:
        if is_glob(root):
            try:
                names = sorted(os.listdir(root.parent))
            except OSError:
                continue
            for f in names:
                p = root.parent / f
                if (
                    f.startswith(".")
                    or p.suffix.casefold() not in policy.AUDIO_EXT
                    or not fnmatch.fnmatch(f.casefold(), root.name.casefold())
                    or p.is_symlink()
                    or policy.denied(p) is not None
                ):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if not p.is_file():
                    continue
                bpm, key = parse_name(p.stem)
                rows.append(
                    (
                        _canon(p),
                        p.stem,
                        _canon(root.parent),
                        p.suffix.casefold(),
                        st.st_size,
                        st.st_mtime,
                        bpm,
                        key,
                    )
                )
            continue
        if root.is_file():
            if root.is_symlink() or policy.denied(root) is not None:
                continue
            st = root.stat()
            bpm, key = parse_name(root.stem)
            rows.append(
                (
                    _canon(root),
                    root.stem,
                    _canon(root.parent),
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
                        _canon(p),
                        p.stem,
                        _canon(root),
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


def real_path(canon: str) -> Path | None:
    """The path to read a stored row from today: a drive file is found through its volume UUID, whatever the mount is called now; None when that drive is not connected."""
    u = _uuid_of(canon)
    if u is None:
        return Path(canon)
    mount = mounts().get(u)
    if mount is None:
        return None
    rel = canon[len(_VOL_PREFIX) + len(u) :].lstrip("/")
    return mount / rel if rel else mount


def lookup(name: str, db: Path | None = None) -> list[Path]:
    """Indexed sounds whose name (with or without the extension) is exactly `name`, readable now. A sound that exists as wav and mp3 in one folder is returned once, as the wav."""
    want = name.strip().casefold()
    if not want:
        return []
    ents = entries()
    best: dict[tuple[str, str], tuple[int, str]] = {}
    with _db(db) as c:
        for r in c.execute("SELECT path, name, folder, ext FROM samples"):
            if want not in (r["name"].casefold(), (r["name"] + r["ext"]).casefold()):
                continue
            cov = _covering(Path(r["path"]), ents)
            if cov is None or cov[1] != "online":
                continue
            k = (r["folder"], r["name"].casefold())
            rank = 0 if r["ext"].casefold() == ".wav" else 1
            if k not in best or rank < best[k][0]:
                best[k] = (rank, r["path"])
    out = [real_path(p) for _, p in best.values()]
    return [p for p in out if p is not None]


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
        e["on_drive"] = False
    # the same sound in two places (a Desktop copy and a drive copy) is one entry: the copy that is always there, marked "also on the drive"
    merged: dict[tuple, dict] = {}
    for e in by.values():
        k = (
            e["name"].casefold(),
            tuple(e["formats"]),
            e["size"] if len(e["formats"]) == 1 else None,
        )
        if k not in merged:
            merged[k] = e
            continue
        a = merged[k]
        local, drive = (a, e) if not a["path"].startswith(_VOL_PREFIX) else (e, a)
        if local["path"].startswith(_VOL_PREFIX):
            continue  # both on drives: keep the first
        local["on_drive"] = True
        local["drive_where"] = volume_label(Path(drive["path"]))
        local["paths"] = local["paths"] + drive["paths"]
        merged[k] = local
    return list(merged.values())


def scan_new_drives(db: Path | None = None) -> dict:
    """Scan the entries of a drive that has just been connected (or never scanned), once. A drive that stays connected is not walked again, and a drive that is away is only remembered as away."""
    ents = entries()
    by_uuid: dict[str, list[Path]] = {}
    away: set[str] = set()
    for p, st in ents:
        c = _canon(p)
        if not c.startswith(_VOL_PREFIX):
            continue
        u = _uuid_of(c) or ""
        if st == "online":
            by_uuid.setdefault(u, []).append(p)
        elif st == "offline":
            away.add(u)
    files = 0
    with _db(db) as c:
        for u in away:
            c.execute("INSERT OR REPLACE INTO meta VALUES (?, 0)", (f"drive:{u}",))
        known = {r["k"]: r["v"] for r in c.execute("SELECT k, v FROM meta WHERE k LIKE 'drive:%'")}
    for u, roots in by_uuid.items():
        if known.get(f"drive:{u}"):
            continue
        files += scan(roots, db, include_drives=True)["files"]
        with _db(db) as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES (?, 1)", (f"drive:{u}",))
    return {"files": files}


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
