"""The durable plan store: lock, load, validate, save and fingerprint."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from julius.agents.file_common import (
    _PLAN_STATUSES,
    MOVELIKE,
    FileProblem,
)


class StoreMixin:
    """The durable plan store: lock, load, validate, save and fingerprint."""

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        with self._mutex:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with (self.state_dir / "file_agent.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"version": 2, "plans": {}, "batches": []}
        if self.state_path.stat().st_size > 16 * 1024 * 1024:
            raise FileProblem(
                "history_full",
                "File history has reached its size limit. Archive it before making more changes.",
            )
        data = json.loads(self.state_path.read_text())
        if not isinstance(data, dict) or data.get("version") != 2:
            raise FileProblem(
                "state_version", "File history needs review before more changes can run."
            )
        if not isinstance(data.get("plans"), dict) or not isinstance(data.get("batches"), list):
            raise FileProblem(
                "state_corrupt", "The file history is incomplete. It was left unchanged for review."
            )
        for pid, plan in data["plans"].items():
            self._check_plan(pid, plan)
        if any(pid not in data["plans"] for pid in data["batches"]):
            raise FileProblem(
                "state_corrupt",
                "The file history refers to a missing plan. It was left unchanged for review.",
            )
        return data

    @staticmethod
    def _corrupt(message: str) -> FileProblem:
        return FileProblem("state_corrupt", message + " It was left unchanged for review.")

    def _check_plan(self, pid: str, plan: dict) -> None:
        """Reject a history record whose shape is wrong before any operation trusts it."""
        if (
            not isinstance(plan, dict)
            or plan.get("id") != pid
            or not isinstance(plan.get("ops"), list)
            or not plan["ops"]
            or not isinstance(plan.get("created"), (int, float))
            or not isinstance(plan.get("turn"), int)
            or not isinstance(plan.get("session"), str)
            or plan.get("action")
            not in {"move", "copy", "convert", "rename", "create", "trash", "purge"}
            or plan.get("status") not in _PLAN_STATUSES
        ):
            raise self._corrupt("A file-history record is invalid.")
        for op in plan["ops"]:
            self._check_op(plan, op)

    def _check_op(self, plan: dict, op: dict) -> None:
        if (
            not isinstance(op, dict)
            or not isinstance(op.get("to"), str)
            or "from" not in op
            or not isinstance(op.get("fingerprint"), dict)
            or not {"size", "sha256"} <= op["fingerprint"].keys()
        ):
            raise self._corrupt("An operation record is invalid.")
        if plan["action"] in MOVELIKE | {"copy", "convert"} and (
            not isinstance(op["from"], str)
            or not {"mtime_ns", "device", "inode"} <= op["fingerprint"].keys()
        ):
            raise self._corrupt("A source-file record is invalid.")
        if (
            plan["action"] == "create"
            and plan["status"] == "pending"
            and not isinstance(op.get("content"), str)
        ):
            raise self._corrupt("The new-file preview is missing its content.")

    def _save(self, data: dict[str, Any]) -> None:
        fd, name = tempfile.mkstemp(prefix=".file-state-", dir=self.state_dir)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.state_path)
            directory = os.open(self.state_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _fingerprint(self, path: str | Path) -> dict[str, str | int]:
        p = self.resolve(path, internal=True)
        before = p.stat()
        if not stat.S_ISREG(before.st_mode):
            raise FileProblem("not_file", f"{p.name} is not a regular file.")
        h = hashlib.sha256()
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            opened = os.fstat(f.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise FileProblem("changed", f"{p.name} changed before it could be checked.")
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        after = p.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise FileProblem("changed", f"{p.name} changed while being checked. Please try again.")
        return {
            "size": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "device": after.st_dev,
            "inode": after.st_ino,
            "sha256": h.hexdigest(),
        }
