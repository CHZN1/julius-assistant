"""Persistent local reminders, tasks and calendar drafts. No models or daemon.

Reminder delivery is leased and acknowledged after playback: at least once,
not exactly once, when Julius is running. SQLite serializes concurrent clients.
"""

import builtins
import contextlib
import os
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote


def output(ok: bool, code: str, message: str, **data) -> dict[str, Any]:
    return dict(ok=ok, code=code, message=message, data=data)


def utc_timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Please include a time zone or UTC offset.")
    return parsed.timestamp()


class Planner:
    LIMIT = 1000

    def __init__(self, state_dir: str | Path, clock: Callable = time.time) -> None:
        self.path = Path(state_dir) / "planner.sqlite3"
        self.clock = clock
        self._ready = False

    @contextlib.contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ValueError("Planner database must not be a symbolic link.")
        if self.path.exists() and self.path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Planner history exceeds 16 MiB. Archive it before continuing.")
        target = "file:" + quote(str(self.path)) + "?mode=rw" if self._ready else str(self.path)
        con = sqlite3.connect(target, timeout=5, uri=self._ready)
        os.chmod(self.path, 0o600)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA busy_timeout=5000")
            if not self._ready:
                con.executescript("""
                CREATE TABLE IF NOT EXISTS items (
                  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
                  due REAL, end REAL, status TEXT NOT NULL DEFAULT 'pending',
                  lease TEXT, lease_until REAL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS due_items ON items(kind,status,due);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
            """)
            version = con.execute("SELECT value FROM metadata WHERE key='version'").fetchone()
            if self._ready and not version:
                raise ValueError("Planner metadata is missing; history needs review.")
            if version and version[0] != "1":
                raise ValueError("Planner database version needs review.")
            if not self._ready:
                con.execute("INSERT OR IGNORE INTO metadata VALUES ('version','1')")
                con.commit()
                self._ready = True
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def add(
        self, kind: str, title: str, due: int | float | None = None, end: float | None = None
    ) -> dict[str, Any]:
        if kind not in {"reminder", "task", "event"}:
            raise ValueError("Unsupported planner item.")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 240:
            raise ValueError("Please use a title of 1–240 characters.")
        title = title.strip()
        if kind in {"reminder", "event"} and (
            due is None or not self.clock() < due <= self.clock() + 10 * 366 * 86400
        ):
            raise ValueError("Choose a future time within ten years.")
        if kind == "event" and (end is None or not due < end <= due + 31 * 86400):
            raise ValueError("An event needs an end after its start, within 31 days.")
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            if (
                con.execute(
                    "SELECT count(*) FROM items WHERE status IN ('pending','delivering')"
                ).fetchone()[0]
                >= self.LIMIT
            ):
                return output(
                    False,
                    "full",
                    "The active planner limit was reached. Complete or cancel some items first.",
                )
            same = con.execute(
                "SELECT id FROM items WHERE kind=? AND title=? AND due IS ? AND end IS ? AND status IN ('pending','delivering')",
                (kind, title, due, end),
            ).fetchone()
            if same:
                return output(
                    True,
                    "duplicate",
                    "That item already exists; I did not add another.",
                    id=same[0],
                )
            overlaps = (
                con.execute(
                    "SELECT count(*) FROM items WHERE kind='event' AND status='pending' AND due<? AND end>?",
                    (end, due),
                ).fetchone()[0]
                if kind == "event"
                else 0
            )
            identifier = uuid.uuid4().hex[:12]
            con.execute(
                "INSERT INTO items(id,kind,title,due,end,created) VALUES(?,?,?,?,?,?)",
                (identifier, kind, title, due, end, self.clock()),
            )
        when = " at " + datetime.fromtimestamp(due, UTC).isoformat() if due is not None else ""
        caveat = (
            " Julius must be running to announce it; missed reminders are delivered when it resumes."
            if kind == "reminder"
            else ""
        )
        return output(
            True,
            "created",
            f"Added {kind}: {title}{when}. Reference {identifier}."
            + caveat
            + (" It overlaps another local event." if overlaps else ""),
            id=identifier,
            overlaps=overlaps,
        )

    def list(self, kind: str | None = None) -> dict[str, Any]:
        if kind is not None and kind not in {"reminder", "task", "event"}:
            raise ValueError("Unsupported planner category.")
        with self.db() as con:
            rows = con.execute(
                "SELECT id,kind,title,due,end,status FROM items WHERE status IN ('pending','delivering') AND (? IS NULL OR kind=?) ORDER BY due IS NULL,due,created LIMIT 101",
                (kind, kind),
            ).fetchall()
        items = [dict(r) for r in rows[:100]]
        message = (
            "No active items."
            if not items
            else "\n".join(
                f"{r['id']}: {r['kind']} — {r['title']}"
                + (f" at {datetime.fromtimestamp(r['due'], UTC).isoformat()}" if r["due"] else "")
                for r in items
            )
        )
        if len(rows) > 100:
            message += "\nShowing the first 100; more items exist."
        return output(True, "listed", message, items=items, truncated=len(rows) > 100)

    def finish(
        self, identifier: str, status: str = "cancelled", expected_kind: str | None = None
    ) -> dict[str, bool | str]:
        if status not in {"cancelled", "done"}:
            raise ValueError("Unsupported item status.")
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT kind,status FROM items WHERE id=?", (identifier,)).fetchone()
            if not row:
                return output(
                    False,
                    "not_found",
                    "That reference was not found. List your items to find its exact reference.",
                )
            if expected_kind is not None and row["kind"] != expected_kind:
                return output(
                    False, "wrong_kind", "That reference belongs to a different kind of item."
                )
            if row["status"] == "delivering":
                return output(
                    False, "busy", "That reminder is being announced now. Try again after delivery."
                )
            if status == "done" and row["kind"] not in {"task", "reminder"}:
                return output(False, "wrong_kind", "Only tasks and reminders can be marked done.")
            if row["status"] == "done" and status == "done":
                return output(True, "done", f"Item {identifier} already done.")
            if row["status"] != "pending" and not (
                status == "done" and row["kind"] == "reminder" and row["status"] == "delivered"
            ):
                return output(False, "inactive", "That item is already inactive.")
            con.execute("UPDATE items SET status=? WHERE id=?", (status, identifier))
        return output(True, status, f"Item {identifier} {status}.")

    def finish_named(
        self, kind: str, name: str, status: str = "cancelled"
    ) -> dict[str, bool | str]:
        name = name.strip().strip('"')
        with self.db() as con:
            rows = con.execute(
                "SELECT id,title FROM items WHERE kind=? AND (status='pending' OR (?='done' AND kind='reminder' AND status='delivered')) LIMIT 1001",
                (kind, status),
            ).fetchall()
        matches = [r for r in rows if r["id"] == name or r["title"].casefold() == name.casefold()]
        if not matches:
            return output(
                False,
                "not_found",
                "No active item matches that name or reference. List your items to check it.",
            )
        if len(matches) > 1:
            return output(
                False,
                "ambiguous",
                "More than one item has that title. Please use its exact reference from the list.",
            )
        return self.finish(matches[0]["id"], status, kind)

    def claim_due(self, limit: int = 3) -> builtins.list[dict[str, str | float] | Any]:
        now = self.clock()
        token = uuid.uuid4().hex
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                "SELECT id,title,due FROM items WHERE kind='reminder' AND due<=? AND (status='pending' OR (status='delivering' AND lease_until<=?)) ORDER BY due LIMIT ?",
                (now, now, min(3, max(1, limit))),
            ).fetchall()
            for row in rows:
                con.execute(
                    "UPDATE items SET status='delivering',lease=?,lease_until=? WHERE id=?",
                    (token, now + 120, row["id"]),
                )
        return [dict(row, lease=token) for row in rows]

    def acknowledge(self, identifier: str, lease: str) -> bool:
        with self.db() as con:
            count = con.execute(
                "UPDATE items SET status='delivered',lease=NULL,lease_until=NULL WHERE id=? AND status='delivering' AND lease=?",
                (identifier, lease),
            ).rowcount
        return count == 1

    def retry(self, identifier: str, lease: str) -> None:
        with self.db() as con:
            con.execute(
                "UPDATE items SET status='pending',lease=NULL,lease_until=NULL WHERE id=? AND status='delivering' AND lease=?",
                (identifier, lease),
            )
