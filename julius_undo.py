"""Undo across agents (Phase F3g): a bare "undo" undoes the MOST RECENT undoable action of any agent (file change, memory forget/correction, coding merge)
and says which one it undid, instead of whichever handler happens to be first in line.

    undo = UndoCoordinator({"files": Agent(label, marker, can_undo, run), ...})
    undo.observe(turn_number)      call at the start of each turn: an agent whose marker changed since last time acted in the previous turn
    undo.handle(text)              "undo" / "undo that" / "please undo it" -> "Undid the last file change. Moved 2 files back." (None when the text is not a bare undo or nothing can be undone)

"undo the merge" and "bring that back" keep going to their own agents."""
import re
from dataclasses import dataclass
from typing import Callable

BARE = re.compile(r"^(?:please\s+)?undo(?:\s+(?:that|it|the last (?:one|thing|change|action)|my last (?:one|change|action)))?[.!]?$", re.I)
ORDER = ("files", "code", "memory")   # tie-break for actions that predate this run (their relative age is unknown)


@dataclass
class UndoAgent:
    label: str                     # "the last file change"
    marker: Callable[[], object]   # cheap fingerprint of its undoable state; changes whenever it does something new
    can_undo: Callable[[], bool]
    run: Callable[[], object]      # performs the undo, returns the agent's own message (or None if it declined)


class UndoCoordinator:
    def __init__(self, agents):
        self.agents = agents; self.last_turn = {n: 0 for n in agents}; self._marks = {n: self._mark(a) for n, a in agents.items()}

    @staticmethod
    def _mark(a):
        try: return a.marker()
        except Exception: return None

    def observe(self, turn):
        for n, a in self.agents.items():
            m = self._mark(a)
            if m != self._marks[n]: self._marks[n] = m; self.last_turn[n] = turn

    def order(self):
        names = sorted(self.agents, key=lambda n: (-self.last_turn[n], ORDER.index(n) if n in ORDER else 99))
        out = []
        for n in names:
            try:
                if self.agents[n].can_undo(): out.append(n)
            except Exception: pass
        return out

    def handle(self, text):
        if not BARE.match((text or "").strip()): return None
        for n in self.order():
            a = self.agents[n]
            try: msg = a.run()
            except Exception: msg = None
            if msg:
                self._marks[n] = self._mark(a)   # the undo itself is not a new action to undo
                return f"Undid {a.label}. {msg}".strip()
        return None
