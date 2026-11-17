"""Conversations, projects, turns, summaries and scoped notes in one SQLite file (`~/.julius/conversations.sqlite3`, 0600, WAL).

One connection, used under a lock (turns are written on the turn thread; summaries and titles from a background thread). Nothing is ever
deleted permanently here: "delete" sets `deleted_at` (the trash, restorable for 30 days); purging is a separate, explicitly confirmed step
that does not exist yet. Search is local (FTS5, BM25), so free."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

INBOX = "Inbox"
TRASH_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, created REAL NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS conversations(
    id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), title TEXT NOT NULL DEFAULT '',
    title_is_auto INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0, deleted_at REAL, source TEXT);
CREATE TABLE IF NOT EXISTS turns(
    id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL REFERENCES conversations(id), t REAL NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL, tool_calls TEXT, meta TEXT);
CREATE INDEX IF NOT EXISTS turns_by_conversation ON turns(conversation_id, id);
CREATE TABLE IF NOT EXISTS summaries(
    conversation_id INTEGER PRIMARY KEY REFERENCES conversations(id), up_to_turn_id INTEGER NOT NULL, text TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memories(
    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, scope_id INTEGER, text TEXT NOT NULL, created REAL NOT NULL,
    source_turn_id INTEGER, confirmed INTEGER NOT NULL DEFAULT 1, deleted_at REAL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(text, content='turns', content_rowid='id');
CREATE VIRTUAL TABLE IF NOT EXISTS titles_fts USING fts5(title, content='conversations', content_rowid='id');
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, content='memories', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN
    INSERT INTO turns_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS conv_ai AFTER INSERT ON conversations BEGIN
    INSERT INTO titles_fts(rowid, title) VALUES (new.id, new.title); END;
CREATE TRIGGER IF NOT EXISTS conv_au AFTER UPDATE OF title ON conversations BEGIN
    INSERT INTO titles_fts(titles_fts, rowid, title) VALUES ('delete', old.id, old.title);
    INSERT INTO titles_fts(rowid, title) VALUES (new.id, new.title); END;
CREATE TRIGGER IF NOT EXISTS mem_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text); END;
"""

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]*")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "about", "with", "my", "our", "your", "me", "we", "i", "you",
    "it", "is", "was", "were", "be", "that", "this", "what", "conversation", "chat", "thread", "talking", "talk", "said", "say",
}  # fmt: skip


def fts_query(text: str) -> str:
    """His words as an FTS5 query: each meaningful word quoted (no operators get through), any of them may match, prefixes allowed."""
    words = [w.lower().strip("'-") for w in _WORD.findall(text or "")]
    words = [_stem(w) for w in words if w and w not in _STOP][:12]
    return " OR ".join(f'"{w}"*' for w in dict.fromkeys(words) if w)


def _stem(w: str) -> str:
    """A crude plural / -ing stem, matched as a prefix: "sprites" finds "sprite", "loops" finds "loop", "talking" finds "talk"."""
    w = re.sub(r"'s$", "", w)
    for end, keep in (
        ("ies", "y"),
        ("xes", "x"),
        ("ches", "ch"),
        ("shes", "sh"),
        ("ing", ""),
        ("es", "e"),
        ("s", ""),
    ):
        if len(w) > len(end) + 2 and w.endswith(end) and not w.endswith("ss"):
            return w[: -len(end)] + keep
    return w


def default_path() -> Path:
    return Path(
        os.environ.get("JULIUS_CONVERSATIONS_DB")
        or Path.home() / ".julius" / "conversations.sqlite3"
    )


class ConversationStore:
    def __init__(self, path: Path | str | None = None, clock: Any = time.time) -> None:
        self.path = Path(path) if path else default_path()
        self.clock = clock
        self.lock = threading.RLock()
        fresh = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        if fresh:
            os.chmod(self.path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def _q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def _x(self, sql: str, args: tuple = ()) -> int:
        """Runs one statement: the new row's id for an INSERT, else the number of rows changed."""
        with self.lock:
            cur = self.db.execute(sql, args)
            return cur.lastrowid if sql.lstrip().upper().startswith("INSERT") else cur.rowcount

    # ---- settings ----
    def get_kv(self, key: str, default: str = "") -> str:
        rows = self._q("SELECT value FROM kv WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def set_kv(self, key: str, value: str) -> None:
        self._x(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    # ---- projects ----
    def project_id(self, name: str, create: bool = True) -> int | None:
        name = re.sub(r"\s+", " ", (name or INBOX)).strip()[:60] or INBOX
        rows = self._q("SELECT id FROM projects WHERE name=?", (name,))
        if rows:
            return rows[0]["id"]
        if not create:
            return None
        return self._x("INSERT INTO projects(name, created) VALUES(?, ?)", (name, self.clock()))

    def find_project(self, words: str) -> dict | None:
        """The project his words name: exact (any case), else the one whose name has all the words, else None."""
        w = re.sub(r"\s+", " ", (words or "").strip(" .!?")).strip()
        w = re.sub(r"^(?:the|my|our)\s+", "", w, flags=re.I)
        w = re.sub(r"\s+project$", "", w, flags=re.I)
        rows = self._q("SELECT * FROM projects WHERE archived=0")
        for r in rows:
            if r["name"].lower() == w.lower():
                return dict(r)
        parts = [p for p in re.findall(r"[a-z0-9]+", w.lower()) if p not in _STOP]
        hits = [dict(r) for r in rows if parts and all(p in r["name"].lower() for p in parts)]
        return hits[0] if len(hits) == 1 else None

    def projects(self) -> list[dict]:
        return [
            dict(r)
            for r in self._q("SELECT * FROM projects WHERE archived=0 ORDER BY pinned DESC, name")
        ]

    # ---- conversations ----
    def new(
        self,
        title: str = "",
        project: str | None = None,
        source: str | None = None,
        at: float | None = None,
    ) -> int:
        now = at if at is not None else self.clock()
        pid = self.project_id(project or INBOX)
        return self._x(
            "INSERT INTO conversations(project_id, title, title_is_auto, created, updated, source) VALUES(?, ?, ?, ?, ?, ?)",
            (pid, clean_title(title), 0 if title else 1, now, now, source),
        )

    def get(self, cid: int | None) -> dict | None:
        if not cid:
            return None
        rows = self._q(
            "SELECT c.*, p.name AS project, (SELECT COUNT(*) FROM turns t WHERE t.conversation_id=c.id) AS n "
            "FROM conversations c JOIN projects p ON p.id=c.project_id WHERE c.id=?",
            (cid,),
        )
        return dict(rows[0]) if rows else None

    def list(
        self, project_id: int | None = None, limit: int = 50, trash: bool = False
    ) -> list[dict]:
        where = "c.deleted_at IS NOT NULL" if trash else "c.deleted_at IS NULL"
        args: tuple = ()
        if project_id is not None:
            where += " AND c.project_id=?"
            args = (project_id,)
        return [
            dict(r)
            for r in self._q(
                "SELECT c.*, p.name AS project, (SELECT COUNT(*) FROM turns t WHERE t.conversation_id=c.id) AS n "
                f"FROM conversations c JOIN projects p ON p.id=c.project_id WHERE {where} "
                "ORDER BY c.pinned DESC, c.updated DESC LIMIT ?",
                (*args, limit),
            )
        ]

    def rename(self, cid: int, title: str, auto: bool = False) -> bool:
        """His own rename always wins: an automatic title never replaces one he gave."""
        title = clean_title(title)
        if not title:
            return False
        if auto:
            return bool(
                self._x(
                    "UPDATE conversations SET title=? WHERE id=? AND title_is_auto=1", (title, cid)
                )
            )
        return bool(
            self._x("UPDATE conversations SET title=?, title_is_auto=0 WHERE id=?", (title, cid))
        )

    def pin(self, cid: int, on: bool = True) -> None:
        self._x("UPDATE conversations SET pinned=? WHERE id=?", (1 if on else 0, cid))

    def move(self, cid: int, project: str) -> int:
        pid = self.project_id(project)
        self._x("UPDATE conversations SET project_id=? WHERE id=?", (pid, cid))
        return pid

    def trash(self, cid: int) -> None:
        self._x("UPDATE conversations SET deleted_at=? WHERE id=?", (self.clock(), cid))

    def restore(self, cid: int) -> None:
        self._x("UPDATE conversations SET deleted_at=NULL WHERE id=?", (cid,))

    # ---- turns ----
    def add(
        self,
        cid: int,
        role: str,
        text: str,
        meta: dict | None = None,
        at: float | None = None,
        tool_calls: Any = None,
    ) -> int:
        now = at if at is not None else self.clock()
        with self.lock:
            tid = self._x(
                "INSERT INTO turns(conversation_id, t, role, text, tool_calls, meta) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    now,
                    role,
                    str(text or ""),
                    json.dumps(tool_calls) if tool_calls else None,
                    json.dumps(meta) if meta else None,
                ),
            )
            self._x("UPDATE conversations SET updated=MAX(updated, ?) WHERE id=?", (now, cid))
        return tid

    def turns(self, cid: int, limit: int | None = None) -> list[dict]:
        if limit is None:
            rows = self._q("SELECT * FROM turns WHERE conversation_id=? ORDER BY id", (cid,))
        else:
            rows = list(
                reversed(
                    self._q(
                        "SELECT * FROM turns WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
                        (cid, limit),
                    )
                )
            )
        return [dict(r) for r in rows]

    def messages(self, cid: int, n: int = 8) -> list[dict]:
        """The last `n` user and assistant messages of one conversation, starting with his message (what the brain sees)."""
        rows = [r for r in self.turns(cid, n) if r["role"] in ("user", "assistant")]
        while rows and rows[0]["role"] != "user":
            rows.pop(0)
        return [{"role": r["role"], "content": r["text"]} for r in rows]

    def count(self, cid: int, role: str | None = None) -> int:
        if role:
            return self._q(
                "SELECT COUNT(*) AS n FROM turns WHERE conversation_id=? AND role=?", (cid, role)
            )[0]["n"]
        return self._q("SELECT COUNT(*) AS n FROM turns WHERE conversation_id=?", (cid,))[0]["n"]

    # ---- search ----
    def search(self, words: str, limit: int = 5, trash: bool = False) -> list[dict]:
        """Conversations his words point to: title matches count most, then matching lines (BM25), with a small recency bonus.
        Each hit carries its best matching line."""
        q = fts_query(words)
        if not q:
            return []
        scores: dict[int, float] = {}
        lines: dict[int, str] = {}
        with self.lock:
            for r in self.db.execute(
                "SELECT rowid AS id, bm25(titles_fts) AS s FROM titles_fts WHERE titles_fts MATCH ? LIMIT 50",
                (q,),
            ):
                scores[r["id"]] = scores.get(r["id"], 0.0) + 3.0 * -r["s"]
            for r in self.db.execute(
                "SELECT t.conversation_id AS id, bm25(turns_fts) AS s, t.text AS text FROM turns_fts JOIN turns t ON t.id=turns_fts.rowid "
                "WHERE turns_fts MATCH ? ORDER BY s LIMIT 200",
                (q,),
            ):
                scores[r["id"]] = scores.get(r["id"], 0.0) + -r["s"]
                lines.setdefault(r["id"], r["text"])
        out = []
        now = self.clock()
        for cid, score in scores.items():
            c = self.get(cid)
            if c is None or (c["deleted_at"] is not None) != trash:
                continue
            age_days = max(0.0, (now - c["updated"]) / 86400)
            c["score"] = round(score + 0.5 / (1 + age_days), 4)
            c["line"] = (lines.get(cid) or "")[:160]
            out.append(c)
        out.sort(key=lambda c: c["score"], reverse=True)
        return out[:limit]

    # ---- summaries ----
    def summary(self, cid: int) -> dict | None:
        rows = self._q("SELECT * FROM summaries WHERE conversation_id=?", (cid,))
        return dict(rows[0]) if rows else None

    def set_summary(self, cid: int, text: str, up_to_turn_id: int) -> None:
        self._x(
            "INSERT INTO summaries(conversation_id, up_to_turn_id, text, updated) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(conversation_id) DO UPDATE SET up_to_turn_id=excluded.up_to_turn_id, text=excluded.text, updated=excluded.updated",
            (cid, up_to_turn_id, text.strip()[:1500], self.clock()),
        )

    # ---- scoped notes ----
    def add_memory(
        self, scope: str, scope_id: int | None, text: str, source_turn_id: int | None = None
    ) -> int:
        if scope not in ("global", "project", "conversation"):
            raise ValueError(scope)
        return self._x(
            "INSERT INTO memories(scope, scope_id, text, created, source_turn_id) VALUES(?, ?, ?, ?, ?)",
            (
                scope,
                scope_id,
                re.sub(r"\s+", " ", text).strip()[:300],
                self.clock(),
                source_turn_id,
            ),
        )

    def memories(self, scope: str, scope_id: int | None) -> list[dict]:
        return [
            dict(r)
            for r in self._q(
                "SELECT * FROM memories WHERE scope=? AND scope_id IS ? AND deleted_at IS NULL ORDER BY id DESC",
                (scope, scope_id),
            )
        ]

    def forget_memory(self, mid: int) -> None:
        self._x("UPDATE memories SET deleted_at=? WHERE id=?", (self.clock(), mid))

    def recall(self, words: str, scope: str, scope_id: int | None, cap: int) -> list[dict]:
        """Up to `cap` notes of one scope that share words with his request (BM25, newer first on a tie)."""
        q = fts_query(words)
        if not q or cap <= 0:
            return []
        rows = self._q(
            "SELECT m.*, bm25(memories_fts) AS s FROM memories_fts JOIN memories m ON m.id=memories_fts.rowid "
            "WHERE memories_fts MATCH ? AND m.scope=? AND m.scope_id IS ? AND m.deleted_at IS NULL ORDER BY s, m.id DESC LIMIT ?",
            (q, scope, scope_id, cap),
        )
        return [dict(r) for r in rows]


def clean_title(title: str) -> str:
    t = re.sub(r"[\x00-\x1f<>]+", " ", str(title or ""))
    t = re.sub(r"\s+", " ", t).strip(" .,!?:;\"'")
    return t[:80]
