"""Local Apple adapter; JSON data never becomes shell/script source."""

import json
import subprocess
import uuid
from unittest.mock import Mock

from julius.brain.planning import Planner, output
from julius.paths import REPO_ROOT


class AppleProvider:
    def __init__(
        self, planner: Planner, config: dict[str, str] | None = None, runner: Mock = subprocess.run
    ) -> None:
        self.planner = planner
        self.runner = runner
        self.config_path = planner.path.parent / "apple_calendar.json"
        self.config = (
            config
            if config is not None
            else (json.loads(self.config_path.read_text()) if self.config_path.exists() else {})
        )

    def call(self, request: dict[str, str | float | int | None]) -> dict[str, bool | str]:
        try:
            process = self.runner(
                [
                    "/usr/bin/osascript",
                    "-l",
                    "JavaScript",
                    str(REPO_ROOT / "native/apple/bridge.js"),
                ],
                input=json.dumps(request),
                text=True,
                capture_output=True,
                timeout=45,
            )
            if process.returncode:
                return output(
                    False, "apple_access", "Apple access failed: " + process.stderr.strip()
                )
            return json.loads(process.stdout)
        except subprocess.TimeoutExpired:
            return output(
                False,
                "apple_unknown",
                "Apple did not respond within 45 seconds. Its result is uncertain; do not create another copy manually before checking.",
            )
        except Exception as exc:
            return output(False, "apple_error", f"Apple operation failed: {exc}")

    def inventory(self):
        return self.call({"action": "inventory"})

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("event") or self.config.get("reminder"))

    def sync(
        self,
        identifier: str,
        kind: str,
        title: str,
        due: int | float,
        end: int | float | None = None,
    ) -> dict[str, bool | str | dict[str, str]]:
        target = self.config.get(kind)
        if not target:
            return output(
                False,
                "not_configured",
                f"No Apple {kind} destination is configured; the item is local only.",
            )
        with self.planner.db() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS apple_links (local_id TEXT PRIMARY KEY,token TEXT NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,remote_id TEXT,status TEXT NOT NULL)"
            )
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM apple_links WHERE local_id=?", (identifier,)
            ).fetchone()
            if not row:
                con.execute(
                    "INSERT INTO apple_links VALUES(?,?,?,?,NULL,'pending')",
                    (identifier, str(uuid.uuid4()), kind, target),
                )
                row = con.execute(
                    "SELECT * FROM apple_links WHERE local_id=?", (identifier,)
                ).fetchone()
            row = dict(row)
        if row["status"] == "synced":
            return output(True, "synced", "Already saved to Apple.", remote_id=row["remote_id"])
        result = self.call(
            dict(
                action="create",
                kind=row["kind"],
                calendar_id=row["target"],
                token=row["token"],
                title=title,
                due=due,
                end=end,
            )
        )
        if result.get("ok"):
            if not result.get("remote_id"):
                return output(
                    False,
                    "apple_unknown",
                    "Apple did not return an item reference. Check the item before retrying.",
                )
            with self.planner.db() as con:
                con.execute(
                    "UPDATE apple_links SET remote_id=?,status='synced' WHERE local_id=?",
                    (result["remote_id"], identifier),
                )
        return result

    def complete(self, identifier: str) -> dict[str, bool | str]:
        return self._finish(identifier, "complete", "done")

    def cancel(self, identifier: str) -> dict[str, bool | str]:
        return self._finish(identifier, "delete", "cancelled")

    def _finish(self, identifier: str, action: str, status: str) -> dict[str, bool | str]:
        with self.planner.db() as con:
            exists = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='apple_links'"
            ).fetchone()
            if not exists:
                return output(True, "local_only", "No Apple item is linked.")
            row = con.execute(
                "SELECT * FROM apple_links WHERE local_id=?", (identifier,)
            ).fetchone()
        if not row:
            return output(True, "local_only", "No Apple item is linked.")
        if action == "complete" and row["kind"] != "reminder":
            return output(False, "wrong_kind", "Only Apple reminders can be completed.")
        if not row["remote_id"]:
            return output(
                False,
                "apple_unknown",
                "This Apple save is unresolved. Check the destination before changing it.",
            )
        result = self.call(
            dict(
                action=action,
                kind=row["kind"],
                calendar_id=row["target"],
                token=row["token"],
                remote_id=row["remote_id"],
            )
        )
        if result.get("ok"):
            with self.planner.db() as con:
                con.execute(
                    "UPDATE apple_links SET status=? WHERE local_id=?", (status, identifier)
                )
        return result
