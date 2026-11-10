"""Cross-agent undo: a bare "undo" goes to the agent that acted most recently, and says which one."""

import re

import julius.agents.files as jf
import julius.brain.tools as jt
import julius.brain.undo as undo
from julius.runtime.session import Session


def _clean(message) -> str:
    return re.sub(r"^(?:Undone\.|Okay,)\s*", "", str(message or "")).strip()


def _file_mark():
    """The latest live file batch and its status, or None."""
    agent = jf.agent()
    with agent._locked():
        state = agent._load()
    live = [p for p in state["batches"] if state["plans"][p]["status"] not in ("undone", "purged")]
    return (live[-1], state["plans"][live[-1]]["status"]) if live else None


def build_undo_coordinator(s: Session) -> undo.UndoCoordinator:
    """Wire the file, coding and memory agents into one coordinator."""

    def file_undo():
        out = jt.dispatcher().human_file(s.file_session, "undo", s.turn)
        return _clean(out["message"]) if out else None

    def code_task():
        return s.code_session.latest("merged")

    def code_mark():
        task = code_task()
        return (task.id, bool(task.merged)) if task else None

    def code_undo():
        out = s.code_session.handle("undo the merge", s.turn)
        return _clean(out["message"]) if out else None

    memory = s.memory

    return undo.UndoCoordinator(
        {
            "files": undo.UndoAgent(
                "the last file change", _file_mark, lambda: _file_mark() is not None, file_undo
            ),
            "code": undo.UndoAgent(
                "the coding merge", code_mark, lambda: code_task() is not None, code_undo
            ),
            "memory": undo.UndoAgent(
                "your last memory change",
                lambda: (id(memory.undo_rows), id(memory.undo_correction)),
                lambda: bool(memory.undo_rows or memory.undo_correction),
                lambda: _clean(memory.handle("undo")),
            ),
        }
    )
