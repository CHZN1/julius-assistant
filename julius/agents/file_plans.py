"""Building previews: move, copy, rename and create plans, history and showing a plan."""

from __future__ import annotations

import builtins
import hashlib
import re
import time
import uuid
from pathlib import Path
from typing import Any

from julius.agents.file_common import (
    FileProblem,
    response,
    result,
    summarize_ops,
)


class PlanMixin:
    """Building previews: move, copy, rename and create plans, history and showing a plan."""

    @response
    def propose(
        self,
        action: str,
        *,
        source: str = ".",
        destination: Any | None = None,
        pattern: str = "*",
        recursive: bool = False,
        path: str | Path | None = None,
        content: Any | None = None,
        turn: int = 0,
        paths: Any | None = None,
        origin: str = "human",
    ) -> dict[str, Any]:
        if action == "trash":
            return self._propose_trash(source, pattern, paths, origin, turn)
        if action not in {"move", "copy", "rename", "create"}:
            raise FileProblem("unsupported_action", "That file action is not supported.")
        if not destination:
            raise FileProblem("missing_destination", "Which folder or filename should I use?")
        dst = self.resolve(destination, new=True)
        self.require_write(dst)
        if action in {"move", "copy"}:
            if dst.exists() and not dst.is_dir():
                raise FileProblem("not_folder", f"{dst.name} is not a folder.")
            src = self.resolve(source)
            ops = self._plan_transfer(action, src, dst, pattern, recursive)
        elif action == "rename":
            src = self.resolve(path)
            ops = self._plan_rename(src, dst)
        else:
            src = None
            ops = self._plan_create(dst, content)
        if not ops:
            raise FileProblem("no_change", "The selected files are already in that location.")
        total = self._fingerprint_ops(ops, content)
        plan = {
            "id": uuid.uuid4().hex[:12],
            "session": self.session,
            "turn": turn,
            "created": time.time(),
            "action": action,
            "status": "pending",
            "ops": ops,
            "total_bytes": total,
            "count": len(ops),
        }
        with self._locked():
            state = self._load()
            state["plans"][plan["id"]] = plan
            self._save(state)
        return result(
            True,
            "preview",
            self._preview_message(action, ops, src, dst)
            + " Nothing has changed. Say yes to confirm or cancel to discard.",
            plan=plan,
        )

    def _plan_transfer(
        self,
        action: str,
        src: Path,
        dst: Path,
        pattern: builtins.list[str] | str,
        recursive: bool,
    ) -> builtins.list[dict[str, str]]:
        if action == "move":
            self.require_write(src)
        hits, scan = self._scan(src, pattern, recursive, limit=self.MAX_PLAN_FILES)
        if scan["truncated"] or scan["issues"]:
            raise FileProblem(
                "incomplete_selection",
                "I could not check the complete selection. Narrow the search before changing files.",
                **scan,
            )
        if not hits:
            raise FileProblem("no_matches", "No matching files were found. Nothing will change.")
        ops = []
        for hit in hits:
            old = Path(hit["path"])
            if old.is_relative_to(dst):
                continue
            # Preserve subfolders when a recursive selection is requested.
            target = dst / old.relative_to(src) if recursive else dst / old.name
            if old == target:
                continue
            ops.append({"from": str(old), "to": str(target)})
        return ops

    def _plan_rename(self, src: Path, dst: Path) -> builtins.list[dict[str, str]]:
        self.require_write(src)
        if dst.parent != src.parent:
            raise FileProblem(
                "rename_location",
                "A rename must keep the file in its current folder. Use move to change folders.",
            )
        if src == dst:
            raise FileProblem("no_change", "That file already has the requested name.")
        return [{"from": str(src), "to": str(dst)}]

    @staticmethod
    def _plan_create(dst: Path, content: str) -> builtins.list[dict[str, str | None]]:
        if not isinstance(content, str):
            raise FileProblem(
                "missing_content", "Please provide the exact text to put in the new file."
            )
        if len(content.encode("utf-8")) > 1024 * 1024:
            raise FileProblem("too_large", "New text files are limited to 1 MB per request.")
        return [{"from": None, "to": str(dst), "content": content}]

    def _fingerprint_ops(
        self, ops: builtins.list[dict[str, str | None]], content: str | None
    ) -> int:
        """Check each target is free, fingerprint each source, and return the total size."""
        too_large = FileProblem(
            "too_large",
            "This selection exceeds the 512 MB batch limit. Split it into smaller requests.",
        )
        total = 0
        for op in ops:
            target = self.resolve(op["to"], new=True, internal=True)
            if target.exists():
                raise FileProblem(
                    "collision",
                    f"{target.name} already exists. Choose another destination; nothing will be overwritten.",
                    path=str(target),
                )
            if op["from"] and total + Path(op["from"]).stat().st_size > self.MAX_PLAN_BYTES:
                raise too_large
            op["fingerprint"] = (
                self._fingerprint(op["from"])
                if op["from"]
                else {
                    "size": len(content.encode("utf-8")),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
            )
            total += op["fingerprint"]["size"]
            op["status"] = "planned"
        if total > self.MAX_PLAN_BYTES:
            raise too_large
        return total

    @staticmethod
    def _preview_message(
        action: str,
        ops: Any,
        src: Path | None,
        dst: Path,
    ) -> str:
        if action in {"move", "copy"}:
            detail = " It covers " + summarize_ops(ops) + "."
            return (
                f"I found {len(ops)} file(s) in {src.name}. I can {action} them to {dst.name}."
                + detail
            )
        if action == "rename":
            return f"I can rename {src.name} to {dst.name}."
        return f"I can create {dst.name} with the supplied text."

    @response
    def history(self, limit: int = 8, today: bool = False) -> dict[str, Any]:
        with self._locked():
            state = self._load()
        rows, midnight, older = [], time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)), None
        for pid in reversed(state["batches"]):
            plan = state["plans"][pid]
            if today and plan["created"] < midnight:
                older = older or time.strftime("%b %d", time.localtime(plan["created"]))
                continue
            when = time.strftime("%b %d %H:%M", time.localtime(plan["created"]))
            verb = {
                "trash": "trashed",
                "move": "moved",
                "copy": "copied",
                "rename": "renamed",
                "create": "created",
            }.get(plan["action"], plan["action"])
            first = Path(plan["ops"][0]["from"] or plan["ops"][0]["to"]).name
            if plan["action"] == "trash":
                first = re.sub(r"^\d+__", "", Path(plan["ops"][0]["to"]).name)
            rows.append(
                {
                    "id": pid,
                    "when": when,
                    "action": plan["action"],
                    "count": len(plan["ops"]),
                    "status": plan["status"],
                    "first": first,
                    "text": f"{when}: {verb} {len(plan['ops'])} file(s), {first}{' and others' if len(plan['ops']) > 1 else ''} ({plan['status']})",
                }
            )
            if len(rows) >= max(1, min(int(limit), 30)):
                break
        if not rows and today:
            return result(
                True,
                "history",
                "I have not changed any files today."
                + (f" The last change was on {older}." if older else ""),
                items=[],
            )
        if not rows:
            return result(True, "history", "I have not changed any files yet.", items=[])
        spoken = "; ".join(r["text"] for r in rows[:3])
        return result(
            True,
            "history",
            f"Recent file changes: {spoken}."
            + (" The rest is on screen." if len(rows) > 3 else ""),
            items=rows,
        )

    @response
    def show(self, plan_id: str) -> dict[str, Any]:
        with self._locked():
            plan = self._load()["plans"].get(plan_id)
        if not plan:
            raise FileProblem("unknown_plan", "I could not find that file plan.")
        return result(
            True,
            "preview",
            f"The plan is to {plan['action']} {len(plan['ops'])} file(s). Its status is {plan['status']}.",
            plan=plan,
        )
