"""Long-term memory store: things the user told Julius, kept locally in SQLite (julius_memory/memory.sqlite3, git-ignored).

Records are short statements. They are found again with full-text search; "forget" is a hard delete (the row, its index entry, and
the turns that said it in the session logs). Nothing here reaches a model except through MemoryAssistant.block()."""

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from julius.paths import REPO_ROOT

STOP = set(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "me",
        "my",
        "of",
        "on",
        "or",
        "our",
        "she",
        "so",
        "some",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "they",
        "this",
        "to",
        "too",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "yours",
        "about",
        "any",
        "anyone",
        "anything",
        "been",
        "being",
        "than",
        "very",
        "really",
        "also",
        "get",
        "got",
        "give",
        "gave",
        "know",
        "knew",
        "like",
        "make",
        "made",
        "more",
        "most",
        "much",
        "need",
        "not",
        "now",
        "one",
        "other",
        "out",
        "over",
        "said",
        "say",
        "see",
        "some",
        "such",
        "tell",
        "told",
        "tonight",
        "today",
        "tomorrow",
        "think",
        "want",
        "way",
        "well",
        "still",
    ]
)
SECRET = re.compile(
    r"\b(password|passcode|pass phrase|passphrase|pin(?: code| number)?|api[ _-]?key|secret key|access token|auth token|ssn|social security|credit card|debit card|card number|cvv|routing number|account number|seed phrase|recovery phrase|private key)\b|sk-[A-Za-z0-9_-]{12,}|\b(?:\d[ -]?){13,19}\b|\b\d{3}-\d{2}-\d{4}\b",
    re.I,
)
SENSITIVE = re.compile(
    r"\b(diagnos\w*|medication|prescri\w*|therap\w*|depress\w*|anxiety|cancer|pregnan\w*|salary|income|debt|mortgage|loan|net worth|bankrupt\w*|lawsuit|divorce\w*|addict\w*|sobriety)\b",
    re.I,
)


def words(text: str) -> list[str | Any]:
    return [
        w
        for w in re.findall(r"[a-z0-9']+", text.lower())
        if (len(w) > 1 or w.isdigit()) and w not in STOP
    ]


def _stem(w: str) -> str:
    if len(w) <= 4:
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith("ing") or w.endswith("ed"):
        return w[:-3] if w.endswith("ing") else w[:-2]
    return w[:-1] if w.endswith("s") and not w.endswith("ss") else w


def jaccard(a: str, b: str) -> float:
    """Word overlap after dropping filler, 'the user' and word endings, so 'the user lives in Springfield' matches 'I live in Springfield'."""
    a, b = {_stem(w) for w in words(a) if w != "user"}, {_stem(w) for w in words(b) if w != "user"}
    return len(a & b) / len(a | b) if a and b else 0.0


def clean(text: str, limit: int = 240) -> str:
    t = re.sub(r"\s+", " ", text or "").strip().strip('"').strip()
    t = re.sub(r"^(?:that|how)\s+", "", t, flags=re.I)  # "remember that ..." leaves the fact
    return (t[:limit].rsplit(" ", 1)[0] if len(t) > limit else t).strip()


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA secure_delete=ON")
        return con

    def _init(self) -> None:
        with self._db() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, kind TEXT DEFAULT 'fact', source TEXT DEFAULT 'said', created REAL, used REAL DEFAULT 0, uses INTEGER DEFAULT 0, utterance TEXT DEFAULT '')"
            )
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(text)")

    def add(
        self, text: str, kind: str = "fact", source: str = "said", utterance: str = ""
    ) -> tuple[Any, ...]:
        """-> (row dict | None, status) with status new | duplicate | updated | refused | empty."""
        text = clean(text)
        if len(words(text)) < 1:
            return None, "empty"
        if SECRET.search(text) or SECRET.search(utterance or ""):
            return None, "refused"
        with self._db() as c:
            for r in c.execute("SELECT * FROM memories").fetchall():
                j = jaccard(text, r["text"])
                if j >= 0.85:
                    return dict(r), "duplicate"
                if j >= 0.6 and not (source == "said" or r["source"] == "auto"):
                    return dict(r), "duplicate"  # an auto note about something you already said
                if (
                    j >= 0.6
                ):  # same subject, new wording: the newer statement replaces the older (an auto note never overwrites something you said)
                    c.execute(
                        "UPDATE memories SET text=?, utterance=?, created=? WHERE id=?",
                        (text, utterance, time.time(), r["id"]),
                    )
                    c.execute("DELETE FROM mem_fts WHERE rowid=?", (r["id"],))
                    c.execute("INSERT INTO mem_fts(rowid,text) VALUES(?,?)", (r["id"], text))
                    return {**dict(r), "text": text, "old": r["text"]}, "updated"
            cur = c.execute(
                "INSERT INTO memories(text,kind,source,created,utterance) VALUES(?,?,?,?,?)",
                (text, kind, source, time.time(), utterance),
            )
            c.execute("INSERT INTO mem_fts(rowid,text) VALUES(?,?)", (cur.lastrowid, text))
            return {
                "id": cur.lastrowid,
                "text": text,
                "kind": kind,
                "source": source,
                "utterance": utterance,
            }, "new"

    def replace(self, row_id: int, text: str, utterance: str = "") -> tuple[Any, ...]:
        """Correct a memory in place (same id); returns (old row, new row) or None."""
        text = clean(text)
        if not text or SECRET.search(text):
            return None
        with self._db() as c:
            old = c.execute("SELECT * FROM memories WHERE id=?", (row_id,)).fetchone()
            if not old:
                return None
            c.execute(
                "UPDATE memories SET text=?, utterance=?, created=?, source='said' WHERE id=?",
                (text, utterance, time.time(), row_id),
            )
            c.execute("DELETE FROM mem_fts WHERE rowid=?", (row_id,))
            c.execute("INSERT INTO mem_fts(rowid,text) VALUES(?,?)", (row_id, text))
        return dict(old), {**dict(old), "text": text, "source": "said"}

    def all(self) -> list[dict[str, str | float | int] | Any]:
        with self._db() as c:
            return [
                dict(r) for r in c.execute("SELECT * FROM memories ORDER BY created DESC, id DESC")
            ]

    def get(self, ids: list[int]) -> list[dict[str, str | float | int]]:
        with self._db() as c:
            return [
                dict(r) for i in ids for r in c.execute("SELECT * FROM memories WHERE id=?", (i,))
            ]

    def search(self, query: str, k: int = 8) -> list[dict[str, str | float | int] | Any]:
        toks = []
        for w in words(query):
            stem = w[: max(4, len(w) - 2)] if len(w) > 5 else w
            toks.append('"{}"*'.format(stem.replace('"', "")))
        if not toks:
            return []
        with self._db() as c:
            try:
                rows = c.execute(
                    "SELECT m.* FROM mem_fts f JOIN memories m ON m.id=f.rowid WHERE mem_fts MATCH ? ORDER BY bm25(mem_fts) LIMIT ?",
                    (" OR ".join(toks), k),
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [dict(r) for r in rows]

    def core(self, n: int = 4) -> list[dict[str, str | float | int] | Any]:
        with self._db() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM memories ORDER BY (kind IN ('preference','person')) DESC, uses DESC, created DESC LIMIT ?",
                    (n,),
                )
            ]

    def touch(self, ids: list[int]) -> None:
        with self._db() as c:
            for i in ids:
                c.execute("UPDATE memories SET used=?, uses=uses+1 WHERE id=?", (time.time(), i))

    def forget(
        self, ids: list[int], sessions_dir: Path | None = None
    ) -> list[dict[str, str | float | int]]:
        """Hard delete: the rows, their index entries, and the session-log turns that mention them. Returns the deleted rows (for one undo)."""
        rows = self.get(ids)
        with self._db() as c:
            for r in rows:
                c.execute("DELETE FROM memories WHERE id=?", (r["id"],))
                c.execute("DELETE FROM mem_fts WHERE rowid=?", (r["id"],))
            c.execute("INSERT INTO mem_fts(mem_fts) VALUES('optimize')")
        still = {r["id"] for r in self.get([r["id"] for r in rows])}
        rows = [
            r for r in rows if r["id"] not in still
        ]  # only what is really gone is reported gone
        if sessions_dir:
            scrub_sessions(
                sessions_dir,
                [x for r in rows for x in (r["text"], r.get("utterance") or "") if len(x) > 8],
            )
        return rows

    def same_utterance(self, utterance: str) -> list[dict[str, str | float | int]]:
        """Every row saved from one spoken sentence (an explicit "remember" and an automatic note of it are two rows)."""
        if not (utterance or "").strip():
            return []
        return [dict(r) for r in self.all() if (r.get("utterance") or "") == utterance]

    def restore(self, rows: list[dict[str, str | float | int]]) -> None:
        for r in rows:
            self.add(
                r["text"], r.get("kind", "fact"), r.get("source", "said"), r.get("utterance", "")
            )

    def export_markdown(self, path: Path) -> None:
        lines = [
            "# What Julius remembers",
            "",
            'Generated from memory.sqlite3. Say "forget ..." to remove something.',
            "",
        ]
        lines += [
            f"- {r['text']}" + (" _(auto)_" if r["source"] == "auto" else "") for r in self.all()
        ]
        Path(path).write_text("\n".join(lines) + "\n")


SCHEMA = REPO_ROOT / "julius_memory.example" / "schema.sql"
SAMPLE = REPO_ROOT / "julius_memory.example" / "sample_memories.json"


def init(path: Path, sample: bool = False) -> MemoryStore:
    """Create an empty memory database at `path`; with sample=True fill it with the invented 'Alex' facts from julius_memory.example/ (never real data)."""
    store = MemoryStore(path)
    if sample:
        for f in json.loads(SAMPLE.read_text())["memories"]:
            store.add(f["text"], f.get("kind", "fact"), f.get("source", "said"), "")
    return store


def scrub_sessions(sessions_dir: Path, needles: list[str]) -> int:
    """Replace every logged turn that contains a forgotten text with a tombstone."""
    needles = [n.lower() for n in needles if n]
    if not needles:
        return 0
    n_changed = 0
    for f in Path(sessions_dir).glob("*.jsonl"):
        out, changed = [], False
        for line in f.read_text().splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                out.append(line)
                continue
            blob = f"{rec.get('user', '')} {rec.get('julius', '')}".lower()
            if any(n in blob for n in needles):
                rec = {"t": rec.get("t"), "user": "[forgotten]", "julius": "[forgotten]"}
                changed = True
                n_changed += 1
            out.append(json.dumps(rec))
        if changed:
            f.write_text("\n".join(out) + "\n")
    return n_changed


if __name__ == "__main__":
    import sys

    if sys.argv[1:2] == ["init"]:
        target = Path(
            next(
                (a for a in sys.argv[2:] if not a.startswith("--")),
                REPO_ROOT / "julius_memory" / "memory.sqlite3",
            )
        )
        if target.exists():
            raise SystemExit(f"{target} already exists; not touching it")
        init(target, "--sample" in sys.argv)
        print(
            f"created {target}"
            + (" with the sample facts" if "--sample" in sys.argv else " (empty)")
        )
    else:
        print("usage: python julius_memstore.py init [path] [--sample]")
