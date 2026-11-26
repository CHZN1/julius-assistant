"""Voice controller for the Opus coding agent: stage -> yes -> run in the background -> show changes -> merge preview -> separate yes.

Like the file session, a task is only ever STAGED by a command or by the planner's code_task tool; starting it takes a spoken yes in the next turn,
and merging into the real project takes another yes after the diff summary has been read out. Nothing here edits a real project except merge().
"""

import contextlib
import difflib
import os
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import julius.agents.code_agent as ca
from julius import log
from julius.agents.code_agent import Task
from julius.agents.file_agent import result
from julius.agents.result import AgentResult
from julius.brain import confirm
from julius.costs import CostTracker

YES = re.compile(
    r"^(?:yes|yeah|yep|yup|ok|okay|sure|go ahead|do it|confirm|confirmed|start it|merge it|apply it|please do)\b",
    re.I,
)
NO = re.compile(r"^(?:no|nope|nah|cancel|never ?mind|don'?t|do not|stop|discard)\b", re.I)
START = re.compile(
    r"^(?:please\s+)?(?:(?:ask|tell|have|get|let)\s+(?:the\s+)?(?:(?:coding|code)\s+agent|opus)\s+(?:to\s+)?|(?:can|could|would|will)\s+(?:the\s+)?(?:(?:coding|code)\s+agent|opus)\s+(?=(?:fix|add|write|build|refactor|implement|change|update|make|create|debug|clean|rename|remove|test)\b)|(?:start|run|begin)\s+(?:a\s+)?(?:coding|code)\s+task\s*(?:to|that|:)?\s*)(?P<goal>.+?)(?:\s+for\s+me)?[?.!]*$",
    re.I,
)
CAP = re.compile(
    r"\b(?:with|up to|limit(?:ed)?(?: it)? to|budget of|spend(?: up to)?|capped at)\s+(?:a\s+)?\$?(?P<n>\d+(?:\.\d+)?)\s*(?:dollars?|bucks|usd)?(?:\s+(?:limit|cap|budget|max))?\b",
    re.I,
)
IN_PROJECT = re.compile(
    r"\s+(?:in|on|for|inside)\s+(?:the\s+|my\s+)?(?P<p>[\w .-]+?)(?:\s+(?:project|folder|repo|repository))?\s*$",
    re.I,
)
FIX_IT = re.compile(
    r"^(?:(?:can|could|would) you |please |ok(?:ay)?,? |now |go ahead and |julius,? )*(?:fix|solve|debug|repair) (?:it|that|this|them|the (?:error|bug|problem|issue|crash)|that (?:error|bug|problem))(?: for me)?(?: please| now)?[?.!]*$",
    re.I,
)
CODE_FILE = re.compile(
    r"\b[\w.-]+\.(?:py|js|ts|tsx|jsx|sh|rb|go|rs|swift|java|kt|c|cc|cpp|h|cs|php|lua)\b"
)
STATUS = re.compile(
    r"(?:how(?:'s| is)|what(?:'s| is)(?: the)? (?:status|state) of)\s+(?:the\s+)?(?:coding|code)\s+(?:task|agent)|(?:coding|code)\s+(?:task|agent)\s+(?:status|progress)",
    re.I,
)
CANCEL = re.compile(r"^(?:cancel|stop|abort)\s+(?:the\s+)?(?:coding|code)\s+(?:task|agent)", re.I)
SHOW = re.compile(
    r"^(?:show|give)\s+(?:me\s+)?(?:the\s+)?(?:coding\s+)?(?:changes|diff)\b|^what did the (?:coding|code) agent (?:change|do)",
    re.I,
)
MERGE = re.compile(
    r"^(?:merge|apply)\s+(?:it|that|them|the\s+(?:coding\s+)?(?:changes|task|result|work)|the (?:coding|code) agent'?s? (?:changes|work))",
    re.I,
)
UNDO_MERGE = re.compile(r"^undo\s+(?:the\s+)?(?:coding\s+)?merge", re.I)
DISCARD = re.compile(
    r"^(?:discard|throw away|delete|drop)\s+(?:the\s+)?(?:coding\s+)?(?:task|changes|result|work|copy)",
    re.I,
)
LOOSE_STATUS = re.compile(
    r"^(?:and\s+|so\s+|okay\s+|ok\s+)?(?:what(?:'s| is| was) (?:the )?(?:status|progress)|how(?:'s| is) (?:it|that|he|the (?:fix|task|job))(?: going| doing| coming along)|is (?:it|that|he|the (?:coding )?(?:task|agent|job)) (?:done|finished|ready|still (?:working|running))|(?:are you|is he) (?:done|finished)|any (?:news|progress|update)|how much longer)\b",
    re.I,
)
PENDING_TTL = 90.0  # a start question stays open this long through a garbled or unrelated utterance
FINISHED = {"done", "capped", "timeout", "failed", "cancelled"}


def _code_card(t: Any, c: dict, what: str) -> None:
    """The changes as a panel card (files and the diff of the scratch copy). The test line is the agent's own report, labelled as such."""
    try:
        from julius.ui_bridge import charts

        if charts._panel is None:
            return
        panel, _files, turn_fn = charts._panel
        sentences = re.split(
            r"(?<=[.!?])\s+|\n+", t.summary or ""
        )  # a dot inside a file name (export_tool.py) is no sentence end
        said = next((x.strip() for x in sentences if re.search(r"\btests?\b", x, re.I)), "")
        panel.code(
            {
                "title": f"Code: {Path(t.project).name}",
                "project": Path(t.project).name,
                "summary": f"Changed {what}. Nothing is in the project until you say merge it.",
                "files": [
                    {"path": n, "status": k} for k in ("added", "modified", "deleted") for n in c[k]
                ],
                "diff": ca.diff_lines(t),
                "tests": f"The agent reports: {said}" if said else "",
            },
            turn=turn_fn(),
        )
    except Exception as exc:  # the card is extra: the spoken answer stands without it
        log.event("code_card", error=type(exc).__name__)


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.casefold())


class CodeSession:
    def __init__(
        self,
        config: dict[str, str] | None = None,
        tracker: CostTracker | None = None,
        api_key: Callable | None = None,
        claude: str | None = None,
        sandbox: bool = True,
        on_done: Callable | None = None,
        projects_root: str | None = None,
    ) -> None:
        self.config = {**ca.DEFAULT_CONFIG, **(config or {})}
        self.tracker, self.sandbox, self.on_done = tracker, sandbox, on_done
        self.api_key = api_key or (lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
        self.claude = claude or self._find_claude()
        self.root = Path(projects_root or self.config["projects_root"]).expanduser()
        self.pending = None  # ("start" | "merge" | "discard", task id)
        self.task = None  # the task this conversation is about

    def _find_claude(self) -> str:
        return shutil.which(self.config["binary"]) or str(Path.home() / ".local" / "bin" / "claude")

    # ---- helpers ----
    def projects(self) -> list[str]:
        return (
            sorted(
                p.name
                for p in self.root.iterdir()
                if p.is_dir() and not p.name.startswith(".") and not ca.FORBIDDEN.search(p.name)
            )
            if self.root.is_dir()
            else []
        )

    def match_project(self, phrase: str) -> str:
        key = _key(
            re.sub(r"\s+(?:project|folder|repo|repository)$", "", phrase.strip(), flags=re.I)
        )
        if not key:
            return None
        names = self.projects()
        exact = [n for n in names if _key(n) == key]
        if exact:
            return exact[0]
        scored = sorted(
            ((difflib.SequenceMatcher(None, key, _key(n)).ratio(), n) for n in names), reverse=True
        )
        if (
            scored
            and scored[0][0] >= 0.85
            and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.1)
        ):
            return scored[0][1]
        return None

    def latest(self, *statuses) -> Task | None:
        """The task a context-sensitive command ("merge it", "show the changes") means: this conversation's, or one finished in the last day."""
        wanted = statuses or FINISHED | {"merged"}
        if self.task:
            try:
                t = ca.load(self.task.id)
                if t.status in wanted:
                    return t
            except ca.CodeProblem:
                pass
        recent = [
            t
            for t in ca.all_tasks()
            if t.status in wanted and time.time() - (t.finished or t.created) < 86400
        ]
        return max(recent, key=lambda t: t.finished or t.created) if recent else None

    def _out(self, ok: bool, code: str, message: str, **data) -> dict[str, Any]:
        return {**result(ok, code, message, **data), "agent": "code"}

    # ---- staging (voice command or planner tool) ----
    def stage(
        self,
        goal: str,
        project: str | None,
        criteria: str = "",
        cap: int | float | None = None,
        origin: str = "human",
        spoken: str = "",
    ) -> dict[str, str | bool | dict[str, float | str]]:
        """spoken: what to call the task out loud when the goal itself is long (the full goal still goes to the agent)."""
        if ca.running():
            return self._out(
                False,
                "busy",
                "A coding task is already running. Say cancel the coding task to stop it first.",
            )
        try:
            name = self.match_project(project) if project else None
            if not name:
                options = ", ".join(self.projects()[:6])
                return self._out(
                    False,
                    "clarification",
                    "Which project should the coding agent work on?"
                    + (f" I can see {options}." if options else ""),
                )
            task = ca.create_task(
                goal,
                name,
                criteria=criteria,
                cap_usd=cap,
                config=self.config,
                projects_root=str(self.root),
            )
        except ca.CodeProblem as exc:
            return self._out(False, exc.code, exc.message)
        self.task, self.pending, self.pending_at = task, ("start", task.id), time.monotonic()
        asked = (
            f" You asked for ${float(cap):.2f}, which is above the limit, so I capped it."
            if cap and float(cap) > float(self.config["max_cap_usd"])
            else ""
        )
        message = (
            f"I can start a coding task on {name}: {spoken or task.goal.rstrip('.')}. It works on a copy, spends at most ${task.cap_usd:.2f} and stops after {task.timeout_s // 60} minutes. "
            f"Nothing in the project changes unless you merge it later.{asked} Say yes to start or cancel."
        )
        return self._out(True, "preview", message, task=task.id, project=name, cap=task.cap_usd)

    # ---- the voice commands ----
    def handle(self, text: str, turn: int = 0) -> AgentResult | None:
        """Run a spoken coding-task command, or return None when the words are not one."""
        text = " ".join(text.strip().split())
        lowered = text.rstrip(".!?").strip()
        if self.pending:
            answered, out = self._answer_pending(text, lowered)
            if answered:
                return out
        if FIX_IT.match(lowered):  # "fix it" right after Julius read an error on the screen
            fixed = self._fix_from_screen()
            if fixed is not None:
                return fixed
        m = START.match(lowered)
        if m:
            return self._start_command(m.group("goal").strip())
        if CANCEL.match(lowered):
            return self._cancel()
        if STATUS.search(lowered) or (
            LOOSE_STATUS.match(lowered)
            and (ca.running() or ((self.latest() or None) and self.latest().status in FINISHED))
        ):
            return self._status()
        for pattern, action in (
            (UNDO_MERGE, self._undo_merge),
            (SHOW, self._show),
            (MERGE, self._merge_preview),
            (DISCARD, self._discard_preview),
        ):
            if pattern.match(lowered):
                return action()
        return None

    def _answer_pending(self, text: str, lowered: str) -> Any:
        """A yes or no to the question just asked: (True, result), else (False, None) to go on."""
        kind, task_id = self.pending
        if YES.match(lowered) or confirm.is_yes(lowered):
            return True, self._confirm(kind, task_id)
        if NO.match(lowered) or confirm.is_no(lowered):
            self.pending = None
            return True, self._decline(kind, task_id)
        fresh = time.monotonic() - getattr(self, "pending_at", 0.0) <= PENDING_TTL
        if (
            kind == "start"
            and fresh
            and len(lowered.split()) >= 7
            and not text.endswith("?")
            and not (START.match(lowered) or CANCEL.match(lowered))
        ):
            return (
                True,
                None,
            )  # garbled / stray speech (his own preview heard back) must not lose the question; merge and discard stay strict
        self.pending = None  # an unrelated request cancels the question, as with files
        return False, None

    def project_with(self, filename: str) -> str | None:
        """The one project under the projects folder that has a file of this name (searched two levels down), else None."""
        hits = []
        for name in self.projects():
            base = self.root / name
            try:
                if (
                    any(base.glob(filename))
                    or any(base.glob(f"*/{filename}"))
                    or any(base.glob(f"*/*/{filename}"))
                ):
                    hits.append(name)
            except OSError:
                continue
        return hits[0] if len(hits) == 1 else None

    def _fix_from_screen(self) -> Any:
        """Stage a fix for the error Julius just read on the screen, in the project that has the file it names. Starting still takes a yes."""
        from julius.agents import vision

        seen = vision.recent_screen()
        if not seen:
            return None
        names = CODE_FILE.findall(seen["answer"] + " " + seen.get("question", ""))
        project = next((p for p in (self.project_with(n) for n in names) if p), None)
        goal = (
            f"Fix the error I just saw on the screen. What it showed: {seen['answer']} "
            "Reproduce it first, fix the cause (not the symptom), and add a test that fails before the fix and passes after it"
        )
        return self.stage(goal, project, spoken="fix that error and add a test that proves the fix")

    def _start_command(self, goal: str) -> dict[str, str | bool | dict[str, float | str]]:
        cap = None
        c = CAP.search(goal)
        if c:
            cap = float(c.group("n"))
            goal = (goal[: c.start()] + goal[c.end() :]).strip(" ,.")
        project = None
        m = IN_PROJECT.search(goal)
        if m:
            project = m.group("p")
            remaining = goal[: m.start()].strip(" ,.")
            if self.match_project(project) and len(remaining) >= 8:
                goal = remaining
            else:
                project = None
        if not project:
            for name in self.projects():  # "fix the tests for demo calc"
                if _key(name) and _key(name) in _key(goal):
                    project = name
                    break
        return self.stage(goal, project, cap=cap)

    def _confirm(self, kind: str, task_id: str) -> dict[str, Any]:
        self.pending = None
        try:
            task = ca.load(task_id)
        except ca.CodeProblem as exc:
            return self._out(False, exc.code, exc.message)
        if kind == "start":
            try:
                ca.start(
                    task,
                    api_key=self.api_key(),
                    tracker=self.tracker,
                    config=self.config,
                    claude=self.claude,
                    sandbox=self.sandbox,
                    on_done=self._done,
                )
            except ca.CodeProblem as exc:
                return self._out(False, exc.code, exc.message)
            return self._out(
                True,
                "started",
                "Started. I'll tell you when it finishes. Say how is the coding task going to check, or cancel the coding task to stop it.",
                task=task.id,
            )
        if kind == "merge":
            try:
                out = ca.merge(task, {"projects_root": str(self.root)})
            except ca.CodeProblem as exc:
                return self._out(False, exc.code, exc.message)
            self.task = task
            n = len(out["added"]) + len(out["modified"])
            skipped = len(out["conflicts"]) + len(out["blocked"])
            extra = (
                f" {skipped} file(s) were skipped because they changed in the project meanwhile or are protected."
                if skipped
                else ""
            )
            return self._out(
                True,
                "merged",
                f"Merged {n} file(s) into {Path(task.project).name}. Originals are backed up, nothing was deleted.{extra} Say undo the merge to put them back.",
                task=task.id,
                merged=out,
            )
        if kind == "discard":
            try:
                ca.discard(task)
            except ca.CodeProblem as exc:
                return self._out(False, exc.code, exc.message)
            self.task = None
            return self._out(
                True, "discarded", "Discarded the coding task copy. Your project was never touched."
            )
        return self._out(False, "unknown", "I'm not sure what to confirm.")

    def _decline(self, kind: str, task_id: str) -> dict[str, bool | str]:
        if kind == "start":
            with contextlib.suppress(ca.CodeProblem):
                ca.discard(ca.load(task_id))
            self.task = None
            return self._out(True, "cancelled", "Okay, I won't start it.")
        return self._out(True, "cancelled", "Okay, nothing was changed.")

    def _done(self, task: Any) -> None:
        if self.on_done:
            self.on_done(task)

    def _status(self) -> dict[str, bool | str | dict[str, str]]:
        live = ca.running()
        if live:
            t = live[0].task
            minutes = max(0, int((time.time() - t.started) / 60))
            return self._out(
                True,
                "status",
                f"The coding task is still running, about {minutes} minute(s) in, with a ${t.cap_usd:.2f} limit.",
                task=t.id,
            )
        t = self.latest()
        if not t:
            return self._out(True, "status", "There is no coding task right now.")
        return self._out(
            True,
            "status",
            ca.spoken_result(t) if t.status in FINISHED else f"The last coding task is {t.status}.",
            task=t.id,
        )

    def _cancel(self) -> dict[str, bool | str]:
        if ca.cancel():
            return self._out(True, "cancelled", "Stopping the coding task.")
        if self.pending:
            self.pending = None
        if self.task and self.task.status == "staged":
            self.task = None
            return self._out(True, "cancelled", "Okay, I won't start it.")
        return self._out(True, "status", "No coding task is running.")

    def _show(self) -> dict[str, Any]:
        t = self.latest()
        if not t:
            return None
        c = ca.changes(t) if t.work.is_dir() else {"added": [], "modified": [], "deleted": []}
        lines = (
            [f"+ {n}" for n in c["added"]]
            + [f"~ {n}" for n in c["modified"]]
            + [f"- {n} (deleted in the copy; a merge would not delete it)" for n in c["deleted"]]
        )
        what = ca.summarize(t) if t.work.is_dir() else "The copy is gone."
        agent = f" The agent said: {t.summary}" if t.summary else ""
        if t.work.is_dir():
            _code_card(t, c, what)
        return self._out(
            True,
            "changes",
            f"The coding task changed {what}.{agent} Say merge it to see exactly what would go into the project.",
            task=t.id,
            lines=lines,
        )

    def _merge_preview(self) -> dict[str, Any]:
        t = self.latest("done", "capped", "timeout", "failed", "cancelled")
        if not t or not t.work.is_dir():
            return None
        try:
            ca.check_project(t.project, str(self.root))
        except ca.CodeProblem as exc:
            return self._out(False, exc.code, exc.message)
        plan = ca.merge_plan(t)
        if plan.get("secrets"):
            listed = "; ".join(
                f"{x['file']} ({', '.join(x['kinds'])})" for x in plan["secrets"][:4]
            )
            return self._out(
                False,
                "secret",
                f"I won't merge: {len(plan['secrets'])} file(s) contain what looks like a key: {listed}. Remove it from the "
                "task's copy, or discard the task.",
                task=t.id,
            )
        n = len(plan["apply"])
        if not n:
            why = (
                f" {len(plan['conflicts'])} file(s) conflict with edits you made since."
                if plan["conflicts"]
                else ""
            )
            return self._out(
                False, "nothing_to_merge", f"There is nothing safe to merge.{why}", task=t.id
            )
        ins, dele = ca.diff_stat(t)
        names = ", ".join(Path(x).name for x in plan["apply"][:4]) + (
            f" and {n - 4} more" if n > 4 else ""
        )
        notes = (
            (
                f" {len(plan['conflicts'])} file(s) will be skipped because you changed them since the copy."
                if plan["conflicts"]
                else ""
            )
            + (
                f" {len(plan['blocked'])} protected file(s) will be skipped."
                if plan["blocked"]
                else ""
            )
            + (
                f" It deleted {len(plan['deleted_not_applied'])} file(s) in the copy; a merge never deletes."
                if plan["deleted_not_applied"]
                else ""
            )
        )
        self.task, self.pending = t, ("merge", t.id)
        return self._out(
            True,
            "preview",
            f"Merging would change {n} file(s) in {Path(t.project).name}: {names}, {ins} lines added and {dele} removed.{notes} Originals are backed up and you can undo it. Say yes to merge or cancel.",
            task=t.id,
            apply=plan["apply"],
            conflicts=plan["conflicts"],
        )

    def _undo_merge(self) -> dict[str, bool | str | dict[str, str]]:
        t = self.latest("merged")
        if not t:
            return None
        restored, removed = ca.undo_merge(t)
        return self._out(
            True,
            "undone",
            f"Undone. Restored {restored} file(s) and removed {removed} added file(s) in {Path(t.project).name}.",
            task=t.id,
        )

    def _discard_preview(self) -> dict[str, bool | str | dict[str, str]]:
        t = self.latest()
        if not t:
            return None
        self.task, self.pending = t, ("discard", t.id)
        return self._out(
            True,
            "preview",
            "I'll delete the scratch copy of the coding task, including its changes. Your project is not touched. Say yes to discard or cancel.",
            task=t.id,
        )
