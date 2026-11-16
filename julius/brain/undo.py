"""Undo across agents (Phase F3g): a bare "undo" undoes the MOST RECENT undoable action of any agent (file change, memory forget/correction, coding merge)
and says which one it undid, instead of whichever handler happens to be first in line.

    undo = UndoCoordinator({"files": Agent(label, marker, can_undo, run), ...})
    undo.observe(turn_number)      call at the start of each turn: an agent whose marker changed since last time acted in the previous turn
    undo.handle(text)              "undo" / "undo that" / "please undo it" -> "Undid the last file change. Moved 2 files back." (None when the text is not a bare undo or nothing can be undone)

"undo the merge" and "bring that back" keep going to their own agents."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

BARE = re.compile(
    r"^(?:please\s+)?undo(?:\s+(?:that|it|the last (?:one|thing|change|action)|my last (?:one|change|action)))?[.!]?$",
    re.I,
)
NAMED = {  # "undo the file change" and friends: that agent, even when something else happened later or the change predates this run
    "files": re.compile(
        r"^(?:please\s+)?undo\s+(?:the|my|that)\s+(?:last\s+)?file\s+(?:change|move|operation)s?[.!]?$",
        re.I,
    ),
    "sprite": re.compile(
        r"^(?:please\s+)?undo\s+(?:the|my|that)\s+(?:last\s+)?sprite(?:\s+(?:edit|change))?[.!]?$",
        re.I,
    ),
}
ORDER = (
    "sprite",
    "files",
    "code",
    "memory",
)  # tie-break inside one turn
NOTHING = "There's nothing from this session to undo. To undo an older file change, say undo the file change."


@dataclass
class UndoAgent:
    label: str  # "the last file change"
    marker: Callable[
        [], object
    ]  # cheap fingerprint of its undoable state; changes whenever it does something new
    can_undo: Callable[[], bool]
    run: Callable[
        [], object
    ]  # performs the undo, returns the agent's own message (or None if it declined)


class UndoCoordinator:
    def __init__(self, agents: dict[str, UndoAgent]) -> None:
        self.agents = agents
        self.last_turn = {n: 0 for n in agents}
        self._marks = {n: self._mark(a) for n, a in agents.items()}

    @staticmethod
    def _mark(a: UndoAgent) -> int:
        try:
            return a.marker()
        except Exception:
            return None

    def observe(self, turn: int) -> None:
        for n, a in self.agents.items():
            m = self._mark(a)
            if m != self._marks[n]:
                self._marks[n] = m
                self.last_turn[n] = turn

    def order(self) -> list[str | Any]:
        names = sorted(
            self.agents, key=lambda n: (-self.last_turn[n], ORDER.index(n) if n in ORDER else 99)
        )
        out = []
        for n in names:
            try:
                if self.agents[n].can_undo():
                    out.append(n)
            except Exception:
                pass
        return out

    def handle(self, text: str) -> str:
        """A bare "undo" undoes the most recent action of THIS session (a sprite edit, a file change, a merge, a memory change) and never reaches back
        to something done before Julius started (that once undid a month-old file move): those need the agent named ("undo the file change")."""
        t = (text or "").strip()
        for n, pat in NAMED.items():
            if n in self.agents and pat.match(t):
                return (
                    self._run([n])
                    or f"There's no {self.agents[n].label.removeprefix('the last ')} to undo."
                )
        if not BARE.match(t):
            return None
        recent = [n for n in self.order() if self.last_turn.get(n, 0) > 0]
        return self._run(recent) or NOTHING

    def _run(self, names: list[str]) -> str | None:
        for n in names:
            if not self.agents[n].can_undo():
                continue
            a = self.agents[n]
            try:
                msg = a.run()
            except Exception:
                msg = None
            if msg:
                self._marks[n] = self._mark(a)  # the undo itself is not a new action to undo
                return f"Undid {a.label}. {msg}".strip()
        return None
