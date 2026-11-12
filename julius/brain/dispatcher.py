"""Small synchronous specialist dispatcher; no models, background jobs or retries.

File writes stay in the trusted human FileSession. Network tools retain their
transport timeouts. A slow synchronous operation is reported as slow, never
claimed cancelled while it might still be running.
"""

import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from julius.agents.file_agent import FileAgent
from julius.agents.file_session import FileSession
from julius.agents.utility_session import UtilitySession

OWNERS = {
    "read_document": "documents",
    "list_planner_items": "planning",
    "set_reminder": "reminders",
    "list_reminders": "reminders",
    "cancel_reminder": "reminders",
    "convert_units": "calculator",
    "date_calculate": "time",
    "get_datetime": "time",
    "get_weather": "weather",
    "get_forecast": "weather",
    "get_scores": "sports",
    "get_quote": "markets",
    "calculate": "calculator",
    "web_search": "research",
    "get_news": "news",
    "remember_fact": "memory",
    "forget_fact": "memory",
    "list_memories": "memory",
    "file_request": "files",
    "code_request": "code",
    "list_files": "files",
    "read_file": "files",
    "find_files": "files",
    "render_chart": "charts",
}

# Read-only network lookups with no shared state may run side by side; files, planning, reminders and documents stay one at a time.
PARALLEL_SAFE = {"time", "weather", "sports", "markets", "calculator", "news", "research"}


_JSON_TYPES = {
    "string": str,
    "integer": int,
    "boolean": bool,
    "number": (int, float),
    "array": list,
    "object": dict,
}


def _check_value(key: str, value: str | bool | float | int, typ: str) -> None:
    """Reject an argument of the wrong type or an empty or oversized string."""
    if typ in _JSON_TYPES and (
        not isinstance(value, _JSON_TYPES[typ])
        or typ in {"integer", "number"}
        and isinstance(value, bool)
    ):
        raise ValueError(f"{key} has the wrong type.")
    if isinstance(value, str) and (not value.strip() or len(value) > 2048):
        raise ValueError(f"{key} must contain 1–2048 characters.")


class Dispatcher:
    def __init__(
        self,
        registry: dict[str, Callable],
        schemas: list[Any],
        file_agent: FileAgent | None = None,
        history_limit: int = 64,
    ) -> None:
        self.registry = registry
        self.schemas = {s["function"]["name"]: s["function"]["parameters"] for s in schemas}
        self.file_agent = file_agent
        self.history = deque(maxlen=history_limit)
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.last_result = None
        self.turn_tasks = deque(maxlen=8)
        self._calls = 0
        self._turn_budget_active = False
        self.turn_call_limit = 8

    def begin_turn(self) -> None:
        self._turn_budget_active = True
        self.turn_tasks.clear()
        self._calls = 0
        self.last_result = None

    def _remember(self, task: dict[str, Any]) -> None:
        metadata = {
            k: task[k] for k in ("id", "agent", "tool", "started_at", "status", "code", "seconds")
        }
        with self._state_lock:
            self.history.append(metadata)
            self.turn_tasks.append(metadata)
            self.last_result = task

    def _validate(self, name: str, args: dict[str, int | str | float | bool]) -> None:
        if name not in OWNERS or name not in self.registry:
            raise ValueError(f"No tool called {name}. That utility is not available.")
        if not isinstance(args, dict):
            raise ValueError("Utility arguments must be an object.")
        schema = self.schemas[name]
        props = schema.get("properties", {})
        if set(args) - set(props):
            raise ValueError("Unexpected utility argument.")
        if any(k not in args for k in schema.get("required", [])):
            raise ValueError("A required utility argument is missing.")
        for key, value in args.items():
            _check_value(key, value, props[key].get("type"))
        self._check_tool_rules(name, args)

    @staticmethod
    def _check_tool_rules(name: str, args: dict[str, int | str | float]) -> None:
        """Per-tool limits that the generic schema cannot express."""
        if name == "get_forecast" and not 1 <= args.get("days", 7) <= 16:
            raise ValueError("Forecast length must be between 1 and 16 days.")
        if name == "get_scores":
            if args.get("league", "mlb").lower() not in {"mlb", "nba", "nfl", "nhl", "wnba"}:
                raise ValueError("That sports league is not supported yet.")
            from julius.brain.score_days import resolve_days

            resolve_days(
                args.get("day", "today")
            )  # raises a speakable ValueError when the day can't be read
        if name == "get_quote":
            import re

            if not re.fullmatch(r"[A-Za-z0-9^.=&' -]{1,40}", args["symbol"].strip()):
                raise ValueError("Please provide a valid ticker symbol or company name.")

    def dispatch(
        self,
        name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = {
            "id": uuid.uuid4().hex,
            "agent": OWNERS.get(name, "unknown"),
            "tool": name,
            "started_at": datetime.now(UTC).isoformat(),
            "ok": False,
            "status": "failed",
            "code": "invalid_request",
            "data": {},
        }
        started = time.perf_counter()
        try:
            args = {} if args is None else args
            self._validate(name, args)
            if self._turn_budget_active and self._calls >= self.turn_call_limit:
                raise ValueError("Utility limit reached for this turn; please narrow the request.")
            with self._state_lock:
                self._calls += 1
            # One active utility avoids racing confirmations or adding model residency; parallel-safe lookups skip the lock.
            exclusive = task["agent"] not in PARALLEL_SAFE
            if exclusive and not self._lock.acquire(blocking=False):
                task.update(
                    code="busy",
                    message="Another utility is still working. Please try again when it finishes.",
                )
            else:
                try:
                    direct = {
                        "list_files": ("list", {"directory": args.get("directory", ".")}),
                        "read_file": ("read", {"path": args.get("path")}),
                        "find_files": ("find", args),
                    }
                    # read-only file tools go straight to the file agent; file_request (the human command session) is in the registry
                    if task["agent"] == "files" and self.file_agent is not None and name in direct:
                        method, kwargs = direct[name]
                        out = getattr(self.file_agent, method)(**kwargs)
                    else:
                        out = self.registry[name](**(args or {}))
                    if isinstance(out, dict) and "ok" in out:
                        task.update(
                            ok=out["ok"],
                            code=out["code"],
                            message=out["message"],
                            data=out.get("data", {}),
                        )
                    else:
                        message = str(out)
                        failed = message.startswith(
                            (
                                "Couldn't",
                                "No data for",
                                "No price for",
                                "No timezone data",
                                "Don't have",
                                "Nothing found.",
                            )
                        )
                        task.update(
                            ok=not failed,
                            code="unavailable" if failed else "completed",
                            message=message,
                        )
                    task["status"] = "completed" if task["ok"] else "failed"
                finally:
                    if exclusive:
                        self._lock.release()
        except Exception as exc:
            task.update(
                code="timeout" if "timeout" in type(exc).__name__.lower() else "error",
                message=f"{name} failed: {type(exc).__name__}: {exc}",
            )
        task["seconds"] = round(time.perf_counter() - started, 4)
        # Keep metadata only: file contents and user queries do not linger here.
        self._remember(task)
        return task

    def record_external(
        self, tool: str, ok: bool, message: str, agent: str = "planner"
    ) -> dict[str, str | bool | int]:
        """Record a tool that runs outside the specialist lock (the planner runs other tools inside itself)."""
        task = {
            "id": uuid.uuid4().hex,
            "agent": agent,
            "tool": tool,
            "started_at": datetime.now(UTC).isoformat(),
            "ok": ok,
            "status": "completed" if ok else "failed",
            "code": "completed" if ok else "unavailable",
            "message": message,
            "data": {},
            "seconds": 0,
        }
        self._remember(task)
        return task

    def human_file(self, session: FileSession, text: str, turn: int) -> Any:
        """Only the human controller can preview and approve mutations."""
        if not self._lock.acquire(blocking=False):
            from julius.agents.file_agent import result

            return result(
                False,
                "busy",
                "Another utility is still working. Please try again when it finishes.",
            )
        started = time.perf_counter()
        try:
            out = session.handle(text, turn)
            if out is not None:
                task = dict(
                    out,
                    id=uuid.uuid4().hex,
                    tool="human_file",
                    started_at=datetime.now(UTC).isoformat(),
                    status="partial"
                    if out["code"] == "partial_sync"
                    else "needs_confirmation"
                    if out["code"] == "preview"
                    else "needs_input"
                    if out["code"] == "clarification"
                    else "completed"
                    if out["ok"]
                    else "failed",
                    seconds=round(time.perf_counter() - started, 4),
                )
                self._remember(task)
            return out
        finally:
            self._lock.release()

    def human_utilities(self, session: UtilitySession, text: str, turn: int) -> Any:
        if not self._lock.acquire(blocking=False):
            from julius.brain.planning import output

            return output(
                False,
                "busy",
                "Another utility is still working. Please try again when it finishes.",
            )
        started = time.perf_counter()
        try:
            try:
                out = session.handle(text, turn)
            except Exception as exc:
                from julius.brain.planning import output

                out = output(
                    False,
                    "storage_error",
                    f"The planner could not complete that request: {type(exc).__name__}: {exc}",
                )
            if out is not None:
                task = dict(
                    out,
                    id=uuid.uuid4().hex,
                    agent="planning",
                    tool="human_planning",
                    started_at=datetime.now(UTC).isoformat(),
                    status="partial"
                    if out["code"] == "partial_sync"
                    else "needs_confirmation"
                    if out["code"] == "preview"
                    else "needs_input"
                    if out["code"] == "clarification"
                    else "completed"
                    if out["ok"]
                    else "failed",
                    seconds=round(time.perf_counter() - started, 4),
                )
                self._remember(task)
            return out
        finally:
            self._lock.release()

    def dispatch_many(self, calls: list[Any]) -> list[dict[str, str | bool | float]]:
        """Run several (name, args) calls: concurrently when every one is parallel-safe, else one after another. Tasks keep call order."""
        if len(calls) > 1 and all(OWNERS.get(n) in PARALLEL_SAFE for n, _ in calls):
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=min(len(calls), 6)) as pool:
                return list(pool.map(lambda c: self.dispatch(*c), calls))
        return [self.dispatch(n, a) for n, a in calls]

    def render(self, task: dict[str, Any]) -> str:
        if task["agent"] == "files" and task["data"]:
            import julius.agents.files as jf

            return jf.render(task)
        return task["message"]
