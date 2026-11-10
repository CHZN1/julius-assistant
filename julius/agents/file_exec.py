"""Carrying plans out: confirm, publish files safely, cancel, undo and recover from interruptions."""

from __future__ import annotations

import builtins
import hashlib
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from julius.agents.file_common import (
    MOVELIKE,
    FileProblem,
    response,
    result,
)


class ExecMixin:
    """Carrying plans out: confirm, publish files safely, cancel, undo and recover from interruptions."""

    def _preflight(
        self,
        plan: dict[str, Any],
    ) -> None:
        if plan["session"] != self.session:
            raise FileProblem(
                "stale_session",
                "This preview is from a previous session. Make a new plan before changing files.",
            )
        if time.time() - plan["created"] > self.PLAN_TTL:
            raise FileProblem("expired", "That preview has expired. Please make a new plan.")
        if plan["action"] == "purge":
            return
        for op in plan["ops"]:
            if plan["action"] != "copy" and op["from"]:
                self.require_write(op["from"])
            if plan["action"] != "purge":
                self.require_write(op["to"]) if plan["action"] != "trash" else None
            if op["from"] and not self._matches(op["from"], op["fingerprint"], identity=True):
                raise FileProblem(
                    "changed",
                    "A selected file changed or disappeared. Review a new plan before continuing.",
                    path=op["from"],
                )
            d = self.resolve(op["to"], new=True, internal=True)
            if d.exists():
                raise FileProblem("collision", f"{d.name} now exists. Nothing will be overwritten.")

    @staticmethod
    def _sync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _publish(
        self,
        op: dict[str, str | dict[str, str | int] | None],
        action: str,
        journal: Callable | None = None,
    ) -> dict[str, str | int]:
        dst = self.resolve(op["to"], new=True, internal=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        self.resolve(dst.parent, internal=True)
        src = self.resolve(op["from"], internal=True) if op["from"] else None
        if src and action in MOVELIKE and src.stat().st_dev == dst.parent.stat().st_dev:
            self._publish_by_link(op, src, dst, journal)
        else:
            self._publish_staged(op, action, src, dst, journal)
        self._sync_directory(dst.parent)
        if src and action in MOVELIKE and src.parent != dst.parent:
            self._sync_directory(src.parent)
        return self._fingerprint(dst)

    def _publish_by_link(
        self,
        op: dict[str, str | dict[str, str | int]],
        src: Path,
        dst: Path,
        journal: Callable | None,
    ) -> None:
        """Same-device move: exclusive publication, so a new target cannot be overwritten."""
        op["publication_fingerprint"] = op["fingerprint"]
        if journal:
            journal()
        os.link(src, dst, follow_symlinks=False)
        if not self._matches(src, op["fingerprint"], identity=True) or not self._matches(
            dst, op["fingerprint"]
        ):
            owned = dst.lstat()
            if (owned.st_dev, owned.st_ino) == (
                op["fingerprint"]["device"],
                op["fingerprint"]["inode"],
            ):
                dst.unlink()
            raise FileProblem(
                "changed",
                "The source or destination changed while moving. The original was retained; any unexpected destination needs review.",
            )
        src.unlink()

    def _publish_staged(
        self,
        op: dict[str, str | dict[str, str | int] | None],
        action: str,
        src: Path | None,
        dst: Path,
        journal: Callable,
    ) -> None:
        """Copy to a hidden temp file in the destination folder, verify it, then link it in."""
        fd, temp = tempfile.mkstemp(prefix=".julius-stage-", dir=dst.parent)
        os.close(fd)
        try:
            if src:
                shutil.copy2(src, temp)
            else:
                Path(temp).write_text(op["content"], encoding="utf-8")
            digest = self._verify_staged(op, src, temp)
            staged = Path(temp).stat()
            op["publication_fingerprint"] = {
                "size": staged.st_size,
                "mtime_ns": staged.st_mtime_ns,
                "device": staged.st_dev,
                "inode": staged.st_ino,
                "sha256": digest,
            }
            if journal:
                journal()
            os.link(temp, dst, follow_symlinks=False)
            if src and action in MOVELIKE:
                if not self._matches(src, op["fingerprint"], identity=True):
                    raise FileProblem(
                        "changed",
                        "The original changed and was retained. The copied destination needs review.",
                    )
                src.unlink()
        finally:
            Path(temp).unlink(missing_ok=True)

    def _verify_staged(
        self, op: dict[str, str | dict[str, str | int] | None], src: Path | None, temp: str
    ) -> str:
        """Flush the staged copy and confirm it still matches what was planned; returns its sha256."""
        with open(temp, "rb") as f:
            os.fsync(f.fileno())
        h = hashlib.sha256()
        with open(temp, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() != op["fingerprint"]["sha256"]:
            raise FileProblem(
                "changed", "The source changed while copying. No destination was published."
            )
        if src and not self._matches(src, op["fingerprint"], identity=True):
            raise FileProblem("changed", "The source changed. No destination was published.")
        return h.hexdigest()

    @response
    def execute(self, plan_id: str, *, confirmed: bool = False, turn: int = 0) -> dict[str, Any]:
        if confirmed is not True:
            raise FileProblem(
                "confirmation_required", "Please confirm the displayed file plan before running it."
            )
        with self._locked():
            state = self._load()
            plan = state["plans"].get(plan_id)
            if not plan:
                raise FileProblem("unknown_plan", "I could not find that file plan.")
            if plan["status"] != "pending":
                raise FileProblem(
                    "already_processed",
                    "That plan has already been processed. Review its result or create a new plan.",
                )
            if turn <= plan["turn"]:
                raise FileProblem(
                    "confirmation_required",
                    "Preview the plan first, then confirm it in a separate turn.",
                )
            self._preflight(plan)
            if plan["action"] == "purge":
                return self._execute_purge(state, plan)
            plan["status"] = "running"
            state["batches"].append(plan_id)
            self._save(state)
            for op in plan["ops"]:
                op["status"] = "applying"
                self._save(state)
                try:
                    op["result_fingerprint"] = self._publish(
                        op, plan["action"], journal=lambda: self._save(state)
                    )
                    op["status"] = "completed"
                    op.pop("content", None)
                except (OSError, FileProblem) as exc:
                    op["error"] = str(exc)
                    op["status"] = "needs_review"
                    plan["status"] = "partial"
                    self._save(state)
                    return result(
                        False,
                        "partial",
                        f"Stopped after {sum(o['status'] == 'completed' for o in plan['ops'])} successful changes. The remaining files need review.",
                        plan=plan,
                    )
                self._save(state)
            plan["status"] = "completed"
            self._save(state)
        verbs = {
            "move": "Moved",
            "copy": "Copied",
            "rename": "Renamed",
            "create": "Created",
            "trash": "Moved to the trash:",
        }
        extra = self.trash_notice() if plan["action"] == "trash" else ""
        return result(
            True,
            "completed",
            f"{verbs[plan['action']]} {len(plan['ops'])} file(s). Say undo to reverse the change."
            + extra,
            plan=plan,
        )

    @response
    def cancel(self, plan_id: str) -> dict[str, str | bool]:
        with self._locked():
            state = self._load()
            plan = state["plans"].get(plan_id)
            if not plan or plan["status"] != "pending":
                raise FileProblem("not_pending", "There is no pending plan to cancel.")
            plan["status"] = "cancelled"
            for op in plan["ops"]:
                op.pop("content", None)
            self._save(state)
        return result(True, "cancelled", "Cancelled. No files were changed.")

    @response
    def undo(
        self,
    ) -> dict[str, Any]:
        with self._locked():
            state = self._load()
            candidates = [
                state["plans"][pid]
                for pid in reversed(state["batches"])
                if state["plans"][pid]["status"] not in ("undone", "purged")
            ]
            if not candidates:
                return result(True, "nothing_to_undo", "There are no file changes to undo.")
            plan = candidates[0]
            reversed_count = 0
            issues = []
            for op in reversed(plan["ops"]):
                if self._undo_op(plan, op, state, issues):
                    reversed_count += 1
            plan["status"] = "undo_partial" if issues else "undone"
            self._save(state)
        return result(
            not issues,
            "undo_partial" if issues else "undone",
            f"Reversed {reversed_count} file change(s)."
            + (" Some files were left untouched and need review." if issues else ""),
            plan=plan,
            issues=issues,
        )

    def _undo_op(
        self,
        plan: dict[str, Any],
        op: dict[str, str | dict[str, str | int] | None],
        state: dict[str, Any],
        issues: builtins.list[Any],
    ) -> bool:
        """Reverse one operation. True when a file change was reversed; problems go into issues."""
        if op["status"] == "undone":
            return False
        if op["status"] == "purged":
            issues.append(
                {
                    "path": op["from"],
                    "reason": "It was deleted permanently when the trash was emptied.",
                }
            )
            return False
        dst = self.resolve(op["to"], new=True, internal=True)
        if op["status"] == "undoing" and self._finish_undoing(plan, op, dst, state):
            return True
        # Recover a crash between publication and saving its final status.
        if op["status"] in {"applying", "needs_review"}:
            recovered = self._recover_interrupted(plan, op, dst, state, issues)
            if recovered is not None:
                return recovered
        if op["status"] == "planned":
            op["status"] = "undone"
            self._save(state)
            return False
        expected = op.get(
            "result_fingerprint", op.get("publication_fingerprint", op["fingerprint"])
        )
        if not dst.exists() or not self._matches(dst, expected, identity=True):
            issues.append(
                {
                    "path": op["to"],
                    "reason": "The result was changed or removed; it was left untouched.",
                }
            )
            return False
        if plan["action"] in MOVELIKE:
            if not self._move_back(op, dst, state, issues):
                return False
        else:
            # Only remove the exact, unchanged output Julius created/copied.
            op["status"] = "undoing"
            self._save(state)
            dst.unlink()
            self._sync_directory(dst.parent)
        op["status"] = "undone"
        self._save(state)
        return True

    def _finish_undoing(
        self,
        plan: dict[str, Any],
        op: dict[str, str | dict[str, str | int]],
        dst: Path,
        state: dict[str, Any],
    ) -> bool:
        """An undo that was interrupted after its work finished: mark it done."""
        if (
            plan["action"] in MOVELIKE
            and not dst.exists()
            and self._matches(op["from"], op.get("result_fingerprint", op["fingerprint"]))
        ) or (plan["action"] in {"copy", "create"} and not dst.exists()):
            op["status"] = "undone"
            self._save(state)
            return True
        return False

    def _recover_interrupted(
        self,
        plan: dict[str, Any],
        op: dict[str, str | dict[str, str | int]],
        dst: Path,
        state: dict[str, Any],
        issues: builtins.list[Any],
    ) -> bool | None:
        """None to continue the normal undo, else True (reversed) or False (handled, not reversed)."""
        src_exists = bool(op["from"] and Path(op["from"]).exists())
        if not dst.exists():
            op["status"] = "undone"
            self._save(state)
            return False
        publication = op.get(
            "publication_fingerprint", op["fingerprint"] if plan["action"] in MOVELIKE else {}
        )
        if not publication or not self._matches(dst, publication, identity=True):
            issues.append(
                {
                    "path": op["to"],
                    "reason": "Interrupted operation cannot be resolved automatically.",
                }
            )
            return False
        if not (src_exists and plan["action"] in MOVELIKE):
            return None
        src = Path(op["from"])
        if os.path.samefile(src, dst) and self._matches(src, op["fingerprint"], identity=True):
            dst.unlink()
            op["status"] = "undone"
            self._save(state)
            return True
        issues.append(
            {"path": op["to"], "reason": "Both paths exist after interruption; review is required."}
        )
        return False

    def _move_back(
        self,
        op: dict[str, str | dict[str, str | int]],
        dst: Path,
        state: dict[str, Any],
        issues: builtins.list[Any],
    ) -> bool:
        """Put a moved file back where it came from; False (with an issue) when it cannot be."""
        original = self.resolve(op["from"], new=True, internal=True)
        try:
            self.require_write(original)
        except FileProblem as exc:
            issues.append({"path": str(original), "reason": str(exc)})
            return False
        if original.exists():
            issues.append(
                {
                    "path": str(original),
                    "reason": "The original location is occupied; nothing was overwritten.",
                }
            )
            return False
        reverse = {"from": str(dst), "to": str(original), "fingerprint": self._fingerprint(dst)}
        op["status"] = "undoing"
        self._save(state)
        try:
            self._publish(reverse, "move")
        except (OSError, FileProblem) as exc:
            issues.append({"path": str(dst), "reason": str(exc)})
            return False
        return True
