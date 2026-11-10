"""The recoverable trash: proposing trash plans, sizes and emptying it."""

from __future__ import annotations

import builtins
import contextlib
import os
import re
import stat
import time
import uuid
from pathlib import Path
from typing import Any

from julius.agents.file_common import (
    TRASH_NOTICE_BYTES,
    FileProblem,
    human_size,
    response,
    result,
    summarize_ops,
)


class TrashMixin:
    """The recoverable trash: proposing trash plans, sizes and emptying it."""

    def _propose_trash(
        self, source: str, pattern: str, paths: builtins.list[str] | None, origin: str, turn: int
    ) -> dict[str, Any]:
        pid = uuid.uuid4().hex[:12]
        hits = []
        skipped = []
        if paths:
            if not isinstance(paths, (list, tuple)) or len(paths) > self.MAX_PLAN_FILES:
                raise FileProblem("bad_paths", f"Give up to {self.MAX_PLAN_FILES} file paths.")
            for raw in paths:
                p = self.resolve(raw)
                self.require_write(p)
                reason = self.protection(p, origin)
                if reason:
                    skipped.append({"name": p.name, "path": str(p), "reason": reason})
                else:
                    hits.append({"path": str(p)})
        else:
            src = self.resolve(source)
            self.require_write(src)
            found, scan = self._scan(
                src, pattern, False, limit=self.MAX_PLAN_FILES
            )  # loose files only: never recursive
            if scan["truncated"] or scan["issues"]:
                raise FileProblem(
                    "incomplete_selection",
                    "I could not check the complete selection. Narrow the search before changing files.",
                    **scan,
                )
            for hit in found:
                reason = self.protection(hit["path"], origin)
                if reason:
                    skipped.append({"name": hit["name"], "path": hit["path"], "reason": reason})
                else:
                    hits.append(hit)
        if not hits:
            if skipped:
                raise FileProblem(
                    "all_protected",
                    f"Nothing was moved to the trash: {len(skipped)} matching file(s) are protected, for example {skipped[0]['name']} ({skipped[0]['reason']}).",
                    skipped=skipped,
                )
            raise FileProblem("no_matches", "No matching files were found. Nothing will change.")
        ops = []
        total = 0
        for i, hit in enumerate(hits, 1):
            old = Path(hit["path"])
            target = self.trash_dir / pid / f"{i:03d}__{old.name}"
            fp = self._fingerprint(old)
            total += fp["size"]
            if total > self.MAX_PLAN_BYTES:
                raise FileProblem(
                    "too_large",
                    "This selection exceeds the 512 MB batch limit. Split it into smaller requests.",
                )
            ops.append(
                {"from": str(old), "to": str(target), "fingerprint": fp, "status": "planned"}
            )
        plan = {
            "id": pid,
            "session": self.session,
            "turn": turn,
            "created": time.time(),
            "action": "trash",
            "status": "pending",
            "ops": ops,
            "total_bytes": total,
            "count": len(ops),
            "origin": origin,
            "skipped": skipped,
        }
        with self._locked():
            state = self._load()
            state["plans"][pid] = plan
            self._save(state)
        names = summarize_ops([{"to": o["from"], "fingerprint": o["fingerprint"]} for o in ops])
        msg = f"I can move {len(ops)} file(s) to the trash: {names}. Nothing is deleted and you can say undo to put them back."
        if skipped:
            msg += (
                f" I left {len(skipped)} protected file(s) alone, for example {skipped[0]['name']}."
            )
        return result(
            True,
            "preview",
            msg + " Nothing has changed. Say yes to confirm or cancel to discard.",
            plan=plan,
            skipped=skipped,
        )

    def trash_size(self) -> int:
        total = 0
        if self.trash_dir.exists():
            for root, _dirs, files in os.walk(self.trash_dir):
                for f in files:
                    with contextlib.suppress(OSError):
                        total += os.lstat(os.path.join(root, f)).st_size
        return total

    def trash_notice(self) -> str:
        """Spoken reminder once the trash is over 1 GB, else ''."""
        size = self.trash_size()
        return (
            f" The trash is now {human_size(size)}. Say empty the trash and I will show what is in it first."
            if size > TRASH_NOTICE_BYTES
            else ""
        )

    @response
    def trash_info(self) -> dict[str, str | bool | dict[str, int]]:
        size = self.trash_size()
        n = sum(len(fs) for _r, _d, fs in os.walk(self.trash_dir)) if self.trash_dir.exists() else 0
        return result(
            True,
            "trash_info",
            f"The trash holds {n} file(s), {human_size(size)}."
            + (self.trash_notice() if size > TRASH_NOTICE_BYTES else ""),
            count=n,
            bytes=size,
        )

    @response
    def propose_empty_trash(self, turn: int = 0) -> dict[str, Any]:
        """Permanent deletion of everything in the trash: preview first, confirm in a separate turn. The only operation that cannot be undone."""
        files = []
        if self.trash_dir.exists():
            for root, _dirs, names in os.walk(self.trash_dir):
                for n in sorted(names):
                    f = Path(root) / n
                    try:
                        info = f.lstat()
                    except OSError:
                        continue
                    if stat.S_ISREG(info.st_mode):
                        files.append((f, info))
        if not files:
            raise FileProblem("trash_empty", "The trash is already empty.")
        pid = uuid.uuid4().hex[:12]
        ops = [
            {
                "from": str(f),
                "to": str(f),
                "fingerprint": {"size": i.st_size, "sha256": ""},
                "status": "planned",
            }
            for f, i in files
        ]
        total = sum(i.st_size for _f, i in files)
        plan = {
            "id": pid,
            "session": self.session,
            "turn": turn,
            "created": time.time(),
            "action": "purge",
            "status": "pending",
            "ops": ops,
            "total_bytes": total,
            "count": len(ops),
        }
        with self._locked():
            state = self._load()
            state["plans"][pid] = plan
            self._save(state)
        names = summarize_ops(
            [
                {"to": re.sub(r"^\d+__", "", Path(o["from"]).name), "fingerprint": o["fingerprint"]}
                for o in ops
            ]
        )
        return result(
            True,
            "preview",
            f"The trash holds {len(ops)} file(s), {human_size(total)}: {names}. Emptying it deletes them permanently and cannot be undone. Say yes to confirm or cancel to keep them.",
            plan=plan,
        )

    def _execute_purge(
        self,
        state: dict[str, Any],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        gone = 0
        kept = 0
        for op in plan["ops"]:
            f = self.resolve(op["from"], internal=True)
            try:
                if (
                    f.exists()
                    and f.is_relative_to(self.trash_dir)
                    and f.lstat().st_size == op["fingerprint"]["size"]
                ):
                    f.unlink()
                    gone += 1
                    op["status"] = "purged"
                else:
                    kept += 1
                    op["status"] = "skipped"
            except OSError:
                kept += 1
                op["status"] = "skipped"
        # earlier trash moves can no longer be undone for the files that were purged
        purged = {op["from"] for op in plan["ops"] if op["status"] == "purged"}
        for other in state["plans"].values():
            if other["action"] == "trash":
                for op in other["ops"]:
                    if op["to"] in purged:
                        op["status"] = "purged"
                if all(op["status"] in ("purged", "undone") for op in other["ops"]) and other[
                    "status"
                ] in ("completed", "undo_partial"):
                    other["status"] = "purged"
        for d in sorted(
            (x for x in self.trash_dir.rglob("*") if x.is_dir()),
            key=lambda x: len(x.parts),
            reverse=True,
        ):
            with contextlib.suppress(OSError):
                d.rmdir()
        plan["status"] = "completed"
        self._save(state)
        return result(
            True,
            "purged",
            f"Emptied the trash: {gone} file(s) deleted permanently."
            + (f" {kept} could not be removed." if kept else ""),
            plan=plan,
        )
