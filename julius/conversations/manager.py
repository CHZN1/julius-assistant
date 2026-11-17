"""The current conversation of a running Julius: what is recorded, what the brain sees, and switching.

- Every finished turn (his words and the answer) goes into the current conversation; one is started on the first turn when there is none.
- The brain sees the last 8 messages of the current conversation only (the history), plus, past 16 messages, a short summary (refreshed every
  12 new messages by one Haiku call) and the project and conversation notes that share words with his request (local search, capped).
- Switching never happens under a turn: a command sets the switch, and it is applied when the turn has finished.
- A new conversation gets an automatic title after his third message (one Haiku call); his own rename always wins.
- On a restart the current conversation carries on when it was used in the last 12 hours."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable

from julius import log
from julius.conversations.store import INBOX, ConversationStore

HISTORY_MESSAGES = 8
SUMMARY_AFTER = 16  # messages
SUMMARY_EVERY = 12  # new messages
TITLE_AFTER = 3  # his messages
RESUME_HOURS = 12
CAPS = {"project": 5, "conversation": 3}
NOTE_CHARS = 1600  # about 400 tokens

SUMMARY_SYSTEM = (
    "You keep a running summary of a conversation between Roy and his voice assistant Julius, so Julius can pick it up later. "
    "Write 3 to 5 short plain sentences: what was asked, what was decided or made, and anything left open. No preamble, no lists."
)
TITLE_SYSTEM = "Give a short title (2 to 5 words, no quotes, no final full stop) for a conversation that starts with these messages. Plain words only."
RECAP_SYSTEM = "In 2 or 3 short spoken sentences, say what this earlier conversation between Roy and Julius was about, addressed to Roy ('we talked about...'). No lists."


def haiku(system: str, text: str, max_tokens: int = 250) -> str:
    """One small Haiku call (titles, summaries, recaps), recorded in the cost ledger. Raises when the cloud is unavailable."""
    from julius.brain import cloud as jc

    brain = jc.get_brain()
    cfg = dict(jc.TIERS["haiku"])
    cfg["max_tokens"] = max_tokens
    r = brain.slow_client().messages.create(
        model=brain.config["models"]["haiku"],
        system=system,
        messages=[{"role": "user", "content": text}],
        **cfg,
    )
    brain.tracker.record("haiku", r.usage, note="conversations")
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()


def _transcript(rows: list[dict], chars: int = 6000) -> str:
    lines = [
        f"{'Roy' if r['role'] == 'user' else 'Julius'}: {r['text']}"
        for r in rows
        if r["role"] in ("user", "assistant")
    ]
    out = "\n".join(lines)
    return out[-chars:]


def when(t: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    day = time.strftime("%Y-%m-%d", time.localtime(t))
    if day == time.strftime("%Y-%m-%d", time.localtime(now)):
        return "today"
    if day == time.strftime("%Y-%m-%d", time.localtime(now - 86400)):
        return "yesterday"
    return time.strftime("%b %d", time.localtime(t)).replace(" 0", " ")


class Conversations:
    def __init__(
        self,
        store: ConversationStore,
        llm: Callable[[str, str, int], str] | None = haiku,
        background: bool = True,
    ) -> None:
        self.store = store
        self.llm = llm
        self.background = background
        cur = store.get_kv("current")
        self.current: int | None = int(cur) if cur.isdigit() else None
        self.pending_switch: int | None = None
        self.fresh_pending = False  # switch to "no conversation yet" (the next turn starts one)
        self.pending_delete: tuple[int, float] | None = None
        self.choices: list[dict] = []
        self.command_turn = (
            False  # this turn was a conversation command: not recorded in any conversation
        )
        self._busy: set[tuple[str, int]] = set()

    # ---- the current conversation ----
    def info(self) -> dict | None:
        c = self.store.get(self.current)
        if c is None or c["deleted_at"] is not None:
            return None
        return c

    def resume(self, history: list[dict], now: float | None = None) -> str | None:
        """At start-up: carry on the current conversation when it was used recently (its last 8 messages into the history)."""
        now = time.time() if now is None else now
        c = self.info()
        if c is None or now - c["updated"] > RESUME_HOURS * 3600:
            self.current = None
            return None
        history[:] = history[:1] + self.store.messages(c["id"], HISTORY_MESSAGES)
        return c["title"] or "untitled"

    def ensure(self) -> int:
        if self.info() is None:
            self.current = self.store.new()
            self.store.set_kv("current", str(self.current))
        return self.current

    def switch(self, cid: int | None) -> None:
        """Takes effect when this turn ends (never under a turn in progress)."""
        if cid is None:
            self.fresh_pending, self.pending_switch = True, None
        else:
            self.pending_switch, self.fresh_pending = cid, False

    def after_turn(
        self, history: list[dict], user: str, answer: str, meta: dict | None = None
    ) -> None:
        """Called once a turn has finished: record it (unless it was a conversation command), then apply a pending switch."""
        try:
            if self.command_turn:
                self.command_turn = False
            elif (user or "").strip() and answer is not None:
                cid = self.ensure()
                self.store.add(cid, "user", user, meta)
                self.store.add(cid, "assistant", answer)
                self._maintain(cid)
            if self.pending_switch is not None or self.fresh_pending:
                target, self.pending_switch = self.pending_switch, None
                self.fresh_pending = False
                self.current = target
                self.store.set_kv("current", str(target or ""))
                history[:] = history[:1] + (
                    self.store.messages(target, HISTORY_MESSAGES) if target else []
                )
        except Exception as exc:  # the conversation store must never break a turn
            log.event("conversations_failed", error=type(exc).__name__)

    # ---- titles and summaries (background, one Haiku call each) ----
    def _maintain(self, cid: int) -> None:
        c = self.store.get(cid)
        if c is None:
            return
        jobs = []
        if c["title_is_auto"] and not c["title"] and self.store.count(cid, "user") >= TITLE_AFTER:
            jobs.append(("title", cid))
        if c["n"] > SUMMARY_AFTER:
            s = self.store.summary(cid)
            since = [t for t in self.store.turns(cid) if not s or t["id"] > s["up_to_turn_id"]]
            if not s or len(since) >= SUMMARY_EVERY:
                jobs.append(("summary", cid))
        for job in jobs:
            if job in self._busy:
                continue
            self._busy.add(job)
            if self.background:
                threading.Thread(target=self._run, args=(job,), daemon=True).start()
            else:
                self._run(job)

    def _run(self, job: tuple[str, int]) -> None:
        kind, cid = job
        try:
            if kind == "title":
                self.make_title(cid)
            else:
                self.make_summary(cid)
        except Exception as exc:
            log.event("conversations_" + kind + "_failed", error=type(exc).__name__)
        finally:
            self._busy.discard(job)

    def make_title(self, cid: int) -> str:
        rows = self.store.turns(cid, 12)
        title = ""
        if self.llm is not None:
            try:
                title = self.llm(TITLE_SYSTEM, _transcript(rows, 2000), 20)
            except Exception as exc:
                log.event("conversations_title_llm_failed", error=type(exc).__name__)
        if not title:  # offline: his first words
            first = next((r["text"] for r in rows if r["role"] == "user"), "")
            title = " ".join(first.split()[:6])
        self.store.rename(cid, title.splitlines()[0] if title else "", auto=True)
        return title

    def make_summary(self, cid: int) -> str | None:
        if self.llm is None:
            return None
        old = self.store.summary(cid)
        rows = self.store.turns(cid)
        new = [r for r in rows if not old or r["id"] > old["up_to_turn_id"]]
        if not new:
            return old["text"] if old else None
        text = (
            "Summary so far: " + old["text"] + "\n\nNew messages:\n" if old else "Messages:\n"
        ) + _transcript(new)
        out = self.llm(SUMMARY_SYSTEM, text, 250)
        if out:
            self.store.set_summary(cid, out, rows[-1]["id"])
        return out

    # ---- what the brain sees ----
    def context_block(self) -> str:
        """The conversation's title, project and (past 16 messages) its summary: a SESSION STATE block after the cached prompt."""
        c = self.info()
        if c is None:
            return ""
        lines = [
            f"This conversation: {c['title'] or 'untitled'} (project {c['project']}, {c['n']} messages)."
        ]
        if c["n"] > SUMMARY_AFTER:
            s = self.store.summary(c["id"])
            if s:
                lines.append("Earlier in this conversation (summary): " + s["text"])
        return "SESSION STATE (this conversation, from Julius's records):\n" + "\n".join(
            "- " + x for x in lines
        )

    def notes_block(self, text: str) -> str:
        """Project and conversation notes that share words with his request: narrower scope first, capped, about 400 tokens at most."""
        c = self.info()
        if c is None:
            return ""
        picked = []
        for scope, sid in (("conversation", c["id"]), ("project", c["project_id"])):
            for m in self.store.recall(text, scope, sid, CAPS[scope]):
                picked.append(
                    f"({'this chat' if scope == 'conversation' else c['project'] + ' project'}) {m['text']}"
                )
        out, size = [], 0
        for p in picked:
            if size + len(p) > NOTE_CHARS:
                break
            out.append(p)
            size += len(p)
        return ("Notes that may be relevant:\n" + "\n".join("- " + x for x in out)) if out else ""

    # ---- actions (voice commands and the conversations tool) ----
    def new(self, title: str = "", project: str | None = None) -> str:
        c = self.info()
        if (
            c is not None and c["n"] == 0
        ):  # the current one is still empty: use it rather than leave an empty one behind
            cid = c["id"]
            if title:
                self.store.rename(cid, title)
            if project:
                self.store.move(cid, project)
        else:
            cid = self.store.new(title, project)
        self.switch(cid)
        where = f" in the {project} project" if project and project.lower() != INBOX.lower() else ""
        return f"New conversation{(' about ' + title) if title else ''}{where}. Go ahead."

    def open(self, cid: int, why: str = "") -> str:
        c = self.store.get(cid)
        if c is None or c["deleted_at"] is not None:
            return "I couldn't find that conversation."
        if cid == self.current:
            return f"We're already in {c['title'] or 'that conversation'}."
        self.switch(cid)
        last = self.store.messages(cid, 2)
        tail = ""
        if last:
            tail = " Last thing you said there: " + _quote(
                next((m["content"] for m in last if m["role"] == "user"), last[0]["content"])
            )
        return f"Switched to {c['title'] or 'that conversation'} ({when(c['updated'])}).{tail}"

    def find(self, words: str) -> tuple[dict | None, str | None]:
        """(the conversation his words point to, or None with a sentence: nothing found, or one question naming the two close matches)."""
        hits = self.store.search(words, 3)
        if not hits:
            return None, f"I couldn't find a conversation about {words.strip(' .?!')}."
        if len(hits) > 1 and hits[1]["score"] >= 0.85 * hits[0]["score"]:
            self.choices = hits[:2]
            a, b = (h["title"] or "untitled" for h in hits[:2])
            return (
                None,
                f"Two conversations match: {a} ({when(hits[0]['updated'])}) or {b} ({when(hits[1]['updated'])}). Which one?",
            )
        return hits[0], None

    def switch_to(self, words: str) -> str:
        c, said = self.find(words)
        return said if c is None else self.open(c["id"])

    def switch_project(self, name: str) -> str:
        p = self.store.find_project(name)
        if p is None:
            return f"I don't have a project called {name.strip(' .?!')}. Say move this to the {name.strip(' .?!')} project to start one."
        convs = self.store.list(p["id"], 1)
        if not convs:
            return self.new("", p["name"])
        return self.open(convs[0]["id"])

    def recap(self, words: str) -> str:
        """ "What were we talking about in X": that conversation's summary, without switching to it."""
        c, said = self.find(words)
        if c is None:
            return said
        self.choices = []
        name = c["title"] or "that conversation"
        s = self.store.summary(c["id"])
        if s:
            return f"In {name} ({when(c['updated'])}): {s['text']}"
        rows = self.store.turns(c["id"], 16)
        if self.llm is not None and rows:
            try:
                out = self.llm(RECAP_SYSTEM, _transcript(rows, 4000), 160)
                if out:
                    return f"In {name} ({when(c['updated'])}): {out}"
            except Exception as exc:
                log.event("conversations_recap_failed", error=type(exc).__name__)
        asked = [r["text"] for r in rows if r["role"] == "user"][-3:]
        if not asked:
            return f"{name} has nothing in it yet."
        return (
            f"In {name} ({when(c['updated'])}) you asked: "
            + "; ".join(_quote(a) for a in asked)
            + "."
        )

    def rename(self, title: str) -> str:
        c = self.info()
        if c is None:
            return "We haven't started a conversation yet; say something first, then name it."
        if not self.store.rename(c["id"], title):
            return "That name is empty. Say rename this conversation to and the new name."
        return f"Renamed this conversation to {self.store.get(c['id'])['title']}."

    def pin(self, on: bool = True) -> str:
        c = self.info()
        if c is None:
            return "There's no conversation to pin yet."
        self.store.pin(c["id"], on)
        return f"{'Pinned' if on else 'Unpinned'} {c['title'] or 'this conversation'}."

    def move(self, project: str) -> str:
        c = self.info()
        if c is None:
            c = self.store.get(self.ensure())
        name = re.sub(
            r"^(?:the|my|our)\s+|\s+project$", "", project.strip(" .!?"), flags=re.I
        ).strip()
        if not name:
            return "Which project? Say move this to the, then the project's name."
        found = self.store.find_project(name)
        made = found is None
        self.store.move(c["id"], found["name"] if found else name)
        p = found["name"] if found else name
        return f"Moved {c['title'] or 'this conversation'} to the {p} project" + (
            " (a new project)." if made else "."
        )

    def ask_delete(self) -> str:
        c = self.info()
        if c is None:
            return "There's no conversation to delete."
        self.pending_delete = (c["id"], time.time() + 120)
        return f"Delete {c['title'] or 'this conversation'}? It goes to the trash and can be restored for 30 days. Say yes to delete it."

    def confirm_delete(self) -> str:
        cid, _ = self.pending_delete
        self.pending_delete = None
        c = self.store.get(cid)
        self.store.trash(cid)
        if cid == self.current:
            self.switch(None)
        return f"Moved {c['title'] or 'that conversation'} to the trash. Say restore my last deleted conversation to bring it back."

    def restore(self, words: str = "") -> str:
        hits = (
            self.store.search(words, 1, trash=True)
            if words.strip()
            else self.store.list(trash=True, limit=50)
        )
        if not hits:
            return "There's nothing in the conversation trash" + (
                " that matches." if words.strip() else "."
            )
        c = max(hits, key=lambda h: h["deleted_at"] or 0) if not words.strip() else hits[0]
        self.store.restore(c["id"])
        return f"Restored {c['title'] or 'that conversation'}. Say open the conversation about it to switch to it."

    def listing(self) -> str:
        rows = self.store.list(limit=6)
        if not rows:
            return "No conversations yet."
        parts = [
            f"{'pinned: ' if r['pinned'] else ''}{r['title'] or 'untitled'} ({when(r['updated'])})"
            for r in rows[:5]
        ]
        cur = self.info()
        here = f" We're in {cur['title'] or 'an untitled one'}." if cur else ""
        return "Your recent conversations: " + "; ".join(parts) + "." + here

    def where(self) -> str:
        c = self.info()
        if c is None:
            return "We're in a fresh conversation; it starts with your next message."
        return f"This is {c['title'] or 'an untitled conversation'}, in the {c['project']} project, {c['n']} messages so far."

    def note(self, scope: str, text: str) -> str:
        cid = self.ensure()
        c = self.store.get(cid)
        if scope == "project":
            self.store.add_memory("project", c["project_id"], text)
            return f"Noted for the {c['project']} project: {text}"
        self.store.add_memory("conversation", cid, text)
        return f"Noted for this conversation: {text}"


def _quote(text: str, n: int = 80) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return "“" + (t[: n - 1] + "…" if len(t) > n else t) + "”"
