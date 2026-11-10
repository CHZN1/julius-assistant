"""Scoped file service with UI-ready results and durable, reversible plans.

No model calls. Mutations require a confirmed, current-session preview."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from julius.agents.file_common import FileProblem, human_size, result, summarize_ops
from julius.agents.file_exec import ExecMixin
from julius.agents.file_paths import PathsMixin
from julius.agents.file_plans import PlanMixin
from julius.agents.file_protection import ProtectionMixin
from julius.agents.file_scan import ScanMixin
from julius.agents.file_store import StoreMixin
from julius.agents.file_trash import TrashMixin
from julius.paths import REPO_ROOT


class FileAgent(
    PathsMixin,
    StoreMixin,
    ScanMixin,
    ProtectionMixin,
    PlanMixin,
    TrashMixin,
    ExecMixin,
):
    CAPABILITIES = (
        "find",
        "list",
        "read",
        "move",
        "copy",
        "rename",
        "create",
        "trash",
        "empty_trash",
        "history",
        "preview",
        "cancel",
        "undo",
    )
    MAX_ENTRIES = 10000
    MAX_RESULTS = 200
    MAX_PLAN_FILES = 100
    MAX_PLAN_BYTES = 512 * 1024 * 1024
    PLAN_TTL = 15 * 60

    def __init__(
        self,
        root: Path | None = None,
        state_dir: Path | None = None,
        allowed_roots: None = None,
        trash_dir: None = None,
        folders: dict[str, str] | None = None,
    ) -> None:
        self.root = Path(root or Path.home()).resolve()
        self.modes = self._modes(folders, allowed_roots)
        self.allowed = tuple(p for p, mode in self.modes.items() if mode != "none")
        self.state_dir = Path(state_dir or REPO_ROOT / "julius_memory").resolve()
        self.use_birthtime = True
        self.trash_dir = Path(trash_dir or self.root / ".julius" / "trash").resolve()
        self.state_path = self.state_dir / "file_agent_state.json"
        self.session = uuid.uuid4().hex
        self._mutex = threading.RLock()
        self.last_scan = {}


__all__ = ["FileAgent", "FileProblem", "human_size", "result", "summarize_ops"]
