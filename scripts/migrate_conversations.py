"""Copy Julius's old session logs into the conversations store. Copy only: nothing in julius_memory/ is moved, changed or deleted.

    python scripts/migrate_conversations.py              # dry run (the default): prints what would be created, writes nothing
    python scripts/migrate_conversations.py --apply --yes  # the real run, only after the user's yes

- julius_memory/sessions/<date>.jsonl -> one conversation per file in the Inbox project, titled "<date>: <title>" (the title from one Haiku call
  per file on the real run; the dry run shows the date only). Each line becomes his message and Julius's answer, with the line's time.
- projects.json -> projects (it is empty today, so none are made).
- last_session.json -> the summary of the newest imported conversation.
- memory.sqlite3 facts stay where they are: they are the global memory the brain already uses, so nothing is copied.
- A file already imported (same source name) is skipped, so a second run adds nothing.
- The real run checks, per file, that the turns and the bytes of text in the store equal the file's, and logs the run in ~/dev/MOVE_LOG.md."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from julius.conversations.store import INBOX, ConversationStore, default_path  # noqa: E402
from julius.paths import REPO_ROOT  # noqa: E402

MEM = REPO_ROOT / "julius_memory"
MOVE_LOG = Path.home() / "dev" / "MOVE_LOG.md"


def read_sessions(folder: Path) -> list[dict]:
    """One plan entry per session file: its date, its exchanges (user, julius, time), turn and byte counts."""
    out = []
    for f in sorted(folder.glob("*.jsonl")):
        rows, bad = [], 0
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                bad += 1
                continue
            user, julius = str(rec.get("user") or ""), str(rec.get("julius") or "")
            if not user.strip() and not julius.strip():
                continue
            try:
                at = datetime.fromisoformat(str(rec.get("t"))).timestamp()
            except ValueError:
                at = f.stat().st_mtime
            rows.append({"user": user, "julius": julius, "t": at, "meta": rec.get("meta")})
        out.append(
            {
                "file": f.name,
                "date": f.stem,
                "rows": rows,
                "turns": 2 * len(rows),
                "bytes": sum(len(r["user"].encode()) + len(r["julius"].encode()) for r in rows),
                "bad_lines": bad,
            }
        )
    return out


def facts_count(db: Path) -> int | None:
    if not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        table = next((t for t in ("facts", "memories", "memory") if t in tables), None)
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table else None
        con.close()
        return n
    except sqlite3.Error:
        return None


def plan(store: ConversationStore | None) -> dict:
    sessions = read_sessions(MEM / "sessions")
    done = set()
    if store is not None:
        done = {
            r["source"]
            for r in store._q("SELECT source FROM conversations WHERE source IS NOT NULL")
        }
    projects = (
        json.loads((MEM / "projects.json").read_text()).get("projects", [])
        if (MEM / "projects.json").exists()
        else []
    )
    last = (
        json.loads((MEM / "last_session.json").read_text())
        if (MEM / "last_session.json").exists()
        else {}
    )
    return {
        "sessions": sessions,
        "skip": {s["file"] for s in sessions if f"sessions/{s['file']}" in done},
        "projects": [p.get("name") if isinstance(p, dict) else str(p) for p in projects],
        "last_session": last,
        "facts": facts_count(MEM / "memory.sqlite3"),
    }


def show(p: dict, store_path: Path, applying: bool) -> None:
    todo = [s for s in p["sessions"] if s["file"] not in p["skip"]]
    print(
        f"{'REAL RUN' if applying else 'DRY RUN (nothing is written)'}: conversations store {store_path}"
    )
    print(
        f"Source: {MEM / 'sessions'} ({len(p['sessions'])} files; copy only, the originals are not touched)\n"
    )
    print(
        f"{'file':<18} {'exchanges':>9} {'turns':>6} {'text bytes':>11}  conversation (project {INBOX})"
    )
    for s in p["sessions"]:
        state = (
            "already imported, skipped"
            if s["file"] in p["skip"]
            else f"{s['date']}: <title from Haiku>"
            if applying
            else f"{s['date']}: (title made on the real run)"
        )
        extra = f"  [{s['bad_lines']} unreadable line(s) skipped]" if s["bad_lines"] else ""
        print(
            f"{s['file']:<18} {len(s['rows']):>9} {s['turns']:>6} {s['bytes']:>11}  {state}{extra}"
        )
    print(
        f"\nTo create: {len(todo)} conversations, {sum(s['turns'] for s in todo)} turns, {sum(s['bytes'] for s in todo)} bytes of text"
        f" (skipped as already imported: {len(p['skip'])})."
    )
    print(
        f"Projects from projects.json: {len(p['projects'])}"
        + (f" ({', '.join(p['projects'])})" if p["projects"] else " (none listed)")
    )
    if p["last_session"].get("summary"):
        print(
            f"last_session.json ({p['last_session'].get('date')}): becomes the summary of the {p['last_session'].get('date')} conversation if it is imported."
        )
    print(
        f"Durable facts in memory.sqlite3: {p['facts']} (stay there as the global memory; not copied)."
    )
    print(
        f"Haiku calls on the real run: {len(todo)} (one title per conversation, about $0.0002 each)."
    )


def apply(p: dict, store: ConversationStore, title_fn) -> list[str]:
    """Create the conversations, then verify each against its file. Returns problems (empty when all match)."""
    problems = []
    last = p["last_session"]
    for s in p["sessions"]:
        if s["file"] in p["skip"] or not s["rows"]:
            continue
        first_t = s["rows"][0]["t"]
        cid = store.new("", INBOX, source=f"sessions/{s['file']}", at=first_t)
        for r in s["rows"]:
            store.add(
                cid,
                "user",
                r["user"],
                r["meta"] if isinstance(r["meta"], dict) else None,
                at=r["t"],
            )
            store.add(cid, "assistant", r["julius"], at=r["t"])
        try:
            title = title_fn(s["rows"])
        except Exception as exc:  # offline: his first words
            print(f"  title for {s['file']} from his first words ({type(exc).__name__})")
            title = " ".join(s["rows"][0]["user"].split()[:6])
        store.rename(cid, f"{s['date']}: {title}", auto=True)
        if last.get("date") == s["date"] and last.get("summary"):
            store.set_summary(cid, last["summary"], store.turns(cid)[-1]["id"])
        got = store.turns(cid)
        got_bytes = sum(len(t["text"].encode()) for t in got)
        if len(got) != s["turns"] or got_bytes != s["bytes"]:
            problems.append(
                f"{s['file']}: {len(got)} turns / {got_bytes} bytes in the store, {s['turns']} / {s['bytes']} in the file"
            )
    for name in p["projects"]:
        if name:
            store.project_id(name)
    return problems


def haiku_title(rows: list[dict]) -> str:
    from julius.conversations.manager import TITLE_SYSTEM, haiku

    text = "\n".join(f"User: {r['user']}\nJulius: {r['julius']}" for r in rows[:8])[:3000]
    return haiku(TITLE_SYSTEM, text, 20).splitlines()[0].strip(" .\"'")


def log_run(p: dict, store_path: Path, problems: list[str]) -> None:
    todo = [s for s in p["sessions"] if s["file"] not in p["skip"]]
    lines = [
        f"\n## {time.strftime('%Y-%m-%d %H:%M')}: Julius session logs copied into the conversations store",
        f"- Copied {len(todo)} files from `{MEM / 'sessions'}` into `{store_path}`: {sum(s['turns'] for s in todo)} turns, "
        f"{sum(s['bytes'] for s in todo)} bytes of text. Copy only; the originals were not moved, changed or deleted.",
        "- Verified per file (turns and bytes of text): "
        + ("all match." if not problems else "MISMATCHES: " + "; ".join(problems)),
        f"- Skipped as already imported: {len(p['skip'])}. Facts in memory.sqlite3 left in place ({p['facts']}).",
    ]
    with open(MOVE_LOG, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--apply", action="store_true", help="really create the conversations (needs --yes)"
    )
    ap.add_argument("--yes", action="store_true", help="the user said yes to the real run")
    ap.add_argument(
        "--db",
        default=None,
        help="the conversations store (default ~/.julius/conversations.sqlite3)",
    )
    a = ap.parse_args()
    store_path = Path(a.db) if a.db else default_path()
    if a.apply and not a.yes:
        print("The real run needs --yes (after the user's yes). Nothing was written.")
        return 2
    existing = ConversationStore(store_path) if (a.apply or store_path.exists()) else None
    p = plan(existing)
    show(p, store_path, a.apply)
    if not a.apply:
        return 0
    problems = apply(p, existing, haiku_title)
    log_run(p, store_path, problems)
    print(
        "\nVerified: every file matches."
        if not problems
        else "\nMISMATCHES:\n" + "\n".join(problems)
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
