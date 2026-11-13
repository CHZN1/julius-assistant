"""Opus coding agent, run through `claude -p` inside a sandbox. This module owns everything that makes that safe.

A task never touches a real project. The project is COPIED (minus secrets, caches and git history) to ~/Library/Application Support/Julius/code/<id>/work, a fresh
git repository with no remote is created there, and `claude -p` runs inside a macOS seatbelt profile that denies writing anywhere else and
denies reading your other projects and personal folders. Merging a result back into the real project is a separate, explicit step that
only adds or modifies files that have not changed since the copy, backs up everything it overwrites, never deletes, and can be undone.

Layers, outermost first (any one of them stops the dangerous cases on its own):
  1. project eligibility: only folders directly under the projects root (~/dev), never music, audio or crypto paths, never Julius's own state
  2. a copy with secrets removed and a fresh git repo with no remote (so `git push` has nowhere to go)
  3. seatbelt: no writes outside the task folder, no reads of ~/dev, Documents, Desktop, Downloads, Music, keys or Julius state, network only to a local allowlist proxy that reaches api.anthropic.com
  4. a scrubbed environment: no keys except the one API key the CLI needs, HOME and TMPDIR inside the task folder
  5. `claude --bare` with an allow-list of tools and a deny-list that includes git push, remotes, curl, ssh, rm and package installs
"""

import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from julius.costs import CostTracker

# Not under ~/.julius: the sandbox denies reads of ~/.julius, and with the task folder inside it Claude Code's own path checks fail and every Edit is refused (live, 17:57).
CODE_HOME = Path("~/Library/Application Support/Julius/code").expanduser()
MODEL = "claude-opus-5-5"
FORBIDDEN = re.compile(
    r"(logic|garageband|waves|slate|zebra|kontakt|maschine|stems|music|audio|\bni\b|btc|bitcoin|crypto|ethereum|binance|coinbase|wallet)",
    re.I,
)
SECRET_GLOBS = [
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials*",
    "secrets*",
    "*.keychain*",
    "*.kdbx",
    ".git-credentials",
]
MUSIC_EXT = {
    ".wav",
    ".mp3",
    ".m4a",
    ".aif",
    ".aiff",
    ".flac",
    ".ogg",
    ".aac",
    ".caf",
    ".mid",
    ".midi",
    ".logicx",
    ".band",
    ".als",
    ".nki",
    ".nkx",
    ".ncw",
    ".rex",
    ".sf2",
    ".fxp",
    ".fxb",
}
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    ".venv-mlx-tts",
    "node_modules",
    "__pycache__",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ipynb_checkpoints",
    ".DS_Store",
    ".idea",
    ".tox",
}
MAX_FILES, MAX_BYTES = 3000, 100 * 1024 * 1024
DENIED_READ = [
    "~/dev",
    "~/Documents",
    "~/Desktop",
    "~/Downloads",
    "~/Music",
    "~/Movies",
    "~/Pictures",
    "~/Library/Keychains",
    "~/Library/Mail",
    "~/Library/Messages",
    "~/Library/Safari",
    "~/Library/Application Support/Google",
    "~/.ssh",
    "~/.aws",
    "~/.gnupg",
    "~/.config",
    "~/.claude",
    "~/.julius",
    "~/.netrc",
    "/Library/Audio",
    "/Volumes",
]
TOOLS = "Read,Edit,Write,Grep,Glob,Bash"
ALLOWED = [
    "Read",
    "Edit",
    "Write",
    "Grep",
    "Glob",
    "Bash(python:*)",
    "Bash(python3:*)",
    f"Bash({sys.executable}:*)",
    "Bash(pytest:*)",
    "Bash(ls:*)",
    "Bash(mkdir:*)",
    "Bash(mv:*)",
    "Bash(cp:*)",
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git add:*)",
    "Bash(git commit:*)",
    "Bash(git show:*)",
]
DENIED = [
    "Bash(git push:*)",
    "Bash(git remote:*)",
    "Bash(git config:*)",
    "Bash(git clone:*)",
    "Bash(git fetch:*)",
    "Bash(git pull:*)",
    "Bash(curl:*)",
    "Bash(wget:*)",
    "Bash(ssh:*)",
    "Bash(scp:*)",
    "Bash(rsync:*)",
    "Bash(nc:*)",
    "Bash(sudo:*)",
    "Bash(rm:*)",
    "Bash(chmod:*)",
    "Bash(chown:*)",
    "Bash(pip:*)",
    "Bash(pip3:*)",
    "Bash(brew:*)",
    "Bash(npm:*)",
    "Bash(open:*)",
    "Bash(osascript:*)",
    "Bash(launchctl:*)",
    "WebFetch",
    "WebSearch",
]
RULES = (
    "You are a coding agent working in a throwaway copy of a project. Work only inside the current directory; edit files there and run its tests. "
    "Never try to reach the network, push, add remotes, install packages, delete files, or read outside this directory. Do not touch music or audio files, "
    "anything crypto-related, or secrets. If the task needs any of that, stop and say so. When finished, reply with at most two plain sentences: what you changed and whether the tests pass."
)


class CodeProblem(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Task:
    id: str
    goal: str
    project: str
    criteria: str = ""
    cap_usd: float = 1.5
    timeout_s: int = 1200
    status: str = "staged"  # staged -> running -> done | failed | capped | timeout | cancelled -> merged | discarded
    created: float = field(default_factory=time.time)
    started: float = 0.0
    finished: float = 0.0
    cost_usd: float = 0.0
    summary: str = ""
    baseline: dict = field(default_factory=dict)  # relative path -> sha256 of the copy at the start
    merged: dict = field(default_factory=dict)
    changes: str = ""  # one-line summary of what the work folder now differs by
    stop_reason: str = ""

    @property
    def dir(self) -> Path:
        return CODE_HOME / self.id

    @property
    def work(self) -> Path:
        return self.dir / "work"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def is_secret(name: str) -> bool:
    return any(fnmatch.fnmatch(name.lower(), g.lower()) for g in SECRET_GLOBS)


def safe_name(rel: str) -> bool:
    """True when a relative path may be copied in or merged back: no secrets, no music/crypto names, no git internals."""
    parts = Path(rel).parts
    return not (
        ".git" in parts
        or any(FORBIDDEN.search(p) for p in parts)
        or is_secret(parts[-1])
        or Path(parts[-1]).suffix.lower() in MUSIC_EXT
    )


def check_project(path: str, projects_root: str = "~/dev") -> Path:
    """Resolve and vet a project folder. Returns the real path or raises CodeProblem."""
    root = Path(projects_root).expanduser().resolve()
    raw = Path(os.path.expanduser(str(path)))
    if not raw.is_absolute():
        raw = root / raw
    p = raw.resolve()
    if not p.is_dir():
        raise CodeProblem(
            "no_project", f"I could not find a project folder called {Path(str(path)).name}."
        )
    if p.parent != root:
        raise CodeProblem(
            "outside_projects",
            f"Coding tasks only run on a project folder directly inside {root.name}, not {p.name}.",
        )
    if p.is_relative_to(CODE_HOME.resolve()) or p.name.startswith("."):
        raise CodeProblem("outside_projects", "That folder is not a project I can work on.")
    if FORBIDDEN.search(p.name):
        raise CodeProblem(
            "protected_project",
            f"{p.name} looks like music or crypto work, which the coding agent never touches.",
        )
    return p


def snapshot(folder: Path) -> dict[str, str]:
    """relative path -> sha256 of every ordinary file (no git internals, no symlinks)."""
    out, folder = {}, Path(folder)
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [
            d for d in dirnames if d not in SKIP_DIRS
        ]  # caches the agent's own test runs create are not changes
        for name in filenames:
            if name.endswith((".pyc", ".pyo")) or name == ".DS_Store":
                continue
            p = Path(dirpath) / name
            if not p.is_symlink() and p.is_file():
                out[str(p.relative_to(folder))] = _sha(p)
    return out


def copy_project(src: str, dst: Path) -> tuple[int, list[str]] | tuple[int, list[Any]]:
    """Copy without secrets, caches, symlinks or music/crypto-named files; refuses oversized projects. Returns (files_copied, skipped)."""
    src, dst = Path(src), Path(dst)
    files, total, skipped = [], 0, []
    for dirpath, dirnames, filenames in os.walk(src):
        keep = []
        for d in dirnames:
            full = Path(dirpath) / d
            (
                skipped.append(str(full.relative_to(src)) + "/")
                if d in SKIP_DIRS or full.is_symlink() or FORBIDDEN.search(d)
                else keep.append(d)
            )
        dirnames[:] = keep
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(src)
            if full.is_symlink() or not safe_name(str(rel)) or name in SKIP_DIRS:
                skipped.append(str(rel))
                continue
            total += full.stat().st_size
            files.append(rel)
            if len(files) > MAX_FILES or total > MAX_BYTES:
                raise CodeProblem(
                    "too_large",
                    f"{src.name} is too large for a coding task ({len(files)}+ files, {total // 1_000_000}+ MB). Point me at a smaller project.",
                )
    for rel in files:
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dst / rel)
    return len(files), skipped


def _git(work: Path, *args) -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(work.parent),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    return subprocess.run(
        [
            "git",
            "-C",
            str(work),
            "-c",
            "user.name=Julius",
            "-c",
            "user.email=julius@localhost",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def create_task(
    goal: str,
    project: str,
    criteria: str = "",
    cap_usd: int | float | None = None,
    config: dict[str, str | float | int] | None = None,
    projects_root: str | None = None,
) -> Task:
    """Stage a task: vet the project, clamp the cost cap. Nothing is copied or run yet."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    goal = " ".join(str(goal).split())
    if len(goal) < 8:
        raise CodeProblem("no_goal", "Tell me what the coding task should do.")
    real = check_project(project, projects_root or cfg["projects_root"])
    cap = float(cap_usd) if cap_usd else float(cfg["default_cap_usd"])
    cap = max(0.1, min(cap, float(cfg["max_cap_usd"])))
    task = Task(
        id="c" + uuid.uuid4().hex[:8],
        goal=goal,
        project=str(real),
        criteria=" ".join(str(criteria).split()),
        cap_usd=round(cap, 2),
        timeout_s=int(float(cfg["timeout_minutes"]) * 60),
    )
    save(task)
    return task


def prepare(task: Task) -> tuple[int, list[str]] | tuple[int, list[Any]]:
    """Create the sandbox: copy the project, record the baseline, start a fresh git repo with no remote."""
    if task.work.exists():
        shutil.rmtree(task.work)
    for sub in ("home", "tmp"):
        (task.dir / sub).mkdir(parents=True, exist_ok=True)
    task.work.mkdir(parents=True)
    count, skipped = copy_project(task.project, task.work)
    task.baseline = snapshot(task.work)
    _git(task.work, "init", "-q")
    _git(task.work, "add", "-A")
    _git(task.work, "commit", "-q", "--allow-empty", "-m", "baseline")
    save(task)
    return count, skipped


def seatbelt_profile(task: Task, home: Path | None = None, proxy_port: int | None = None) -> str:
    """macOS sandbox profile text: writes only inside the task folder, no reads of personal folders or other projects, and the ONLY network
    allowed is a connection to the local egress proxy port (julius_egress_proxy.py allows api.anthropic.com there). No proxy port = no network at all."""
    home = Path(home or Path.home()).resolve()
    lines = ["(version 1)", "(allow default)", "(deny file-write*)"]
    d = task.dir.resolve()
    lines.append(
        f'(allow file-write* (subpath "{d}") (literal "/dev/null") (literal "/dev/tty") (regex #"^/dev/(fd|tty)"))'
    )
    for p in DENIED_READ:
        full = (
            Path(os.path.expanduser(p.replace("~", str(home), 1))) if p.startswith("~") else Path(p)
        )
        lines.append(f'(deny file-read* (subpath "{full}"))')
    lines.append(f'(allow file-read* (subpath "{d}"))')
    lines.append("(deny network*)")
    if proxy_port:
        lines.append(f'(allow network-outbound (remote ip "localhost:{int(proxy_port)}"))')
    return "\n".join(lines) + "\n"


def prompt_for(task: Task) -> str:
    text = f"Task: {task.goal}"
    if task.criteria:
        text += f"\nDone means: {task.criteria}"
    return text + "\nWork in the current directory only. Run the project's tests if it has any."


def build_command(
    task: Task,
    api_key: str,
    *,
    claude: str = "claude",
    config: dict[str, Any] | None = None,
    sandbox: bool = True,
    proxy: Any | None = None,
) -> tuple[list[str], dict[str, str], Path]:
    """(argv, env, cwd) for the run. The caller starts it; tests inspect it."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    profile = task.dir / "sandbox.sb"
    profile.write_text(seatbelt_profile(task, proxy_port=proxy.port if proxy else None))
    argv = [
        claude,
        "--bare",
        "-p",
        prompt_for(task),
        "--model",
        cfg["model"],
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "dontAsk",
        "--max-budget-usd",
        f"{task.cap_usd:.2f}",
        "--tools",
        TOOLS,
        "--allowedTools",
        *ALLOWED,
        "--disallowedTools",
        *DENIED,
        "--no-session-persistence",
        "--disable-slash-commands",
        "--strict-mcp-config",
        "--append-system-prompt",
        RULES,
    ]
    if sandbox:
        argv = ["/usr/bin/sandbox-exec", "-f", str(profile)] + argv
    env = {
        "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(task.dir / "home"),
        "TMPDIR": str(task.dir / "tmp"),
        "CLAUDE_CODE_TMPDIR": str(task.dir / "tmp"),
        "LANG": "en_US.UTF-8",
        "ANTHROPIC_API_KEY": api_key,
        "CLAUDE_CODE_SIMPLE": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    if proxy:
        env.update(proxy.env())
    return argv, env, task.work


# ---- results ----
def changes(task: Task) -> dict[str, list[str] | list[Any | str]]:
    """{'added': [...], 'modified': [...], 'deleted': [...]} comparing the work folder with the baseline."""
    now = snapshot(task.work)
    base = task.baseline
    return {
        "added": sorted(set(now) - set(base)),
        "modified": sorted(k for k in now if k in base and now[k] != base[k]),
        "deleted": sorted(set(base) - set(now)),
    }


def diff_stat(task: Task) -> tuple[int, int]:
    """(insertions, deletions) of the work folder against the baseline commit; new files count when staged."""
    try:
        _git(task.work, "add", "-A")
        out = _git(task.work, "diff", "--cached", "--numstat", "HEAD").stdout
    except subprocess.CalledProcessError:
        return 0, 0
    ins = dele = 0
    for line in out.splitlines():
        a, b, *_ = line.split("\t")
        ins += int(a) if a.isdigit() else 0
        dele += int(b) if b.isdigit() else 0
    return ins, dele


def summarize(task: Task) -> str:
    c = changes(task)
    ins, dele = diff_stat(task)
    names = c["added"] + c["modified"]
    shown = ", ".join(Path(n).name for n in names[:4]) + (
        f" and {len(names) - 4} more" if len(names) > 4 else ""
    )
    text = (
        f"{len(c['added'])} new and {len(c['modified'])} changed file(s)"
        + (f": {shown}" if names else "")
        + f", {ins} lines added, {dele} removed"
    )
    if c["deleted"]:
        text += f". It also deleted {len(c['deleted'])} file(s), which a merge will not apply"
    return text


def merge_plan(task: Task) -> dict[str, list[Any | str]]:
    """What a merge would do, checked against the real project right now."""
    c = changes(task)
    real = Path(task.project)
    apply, conflicts, blocked = [], [], []
    for rel in c["added"] + c["modified"]:
        if not safe_name(rel):
            blocked.append(rel)
            continue
        target = real / rel
        if rel in c["added"]:
            (conflicts if target.exists() else apply).append(rel)
        elif not target.is_file() or _sha(target) != task.baseline.get(rel):
            conflicts.append(rel)
        else:
            apply.append(rel)
    return {
        "apply": apply,
        "conflicts": conflicts,
        "blocked": blocked,
        "deleted_not_applied": c["deleted"],
    }


def merge(task: Task, config: dict[str, str] | None = None) -> dict[str, float | list[str]]:
    """Apply the plan: copy added/modified files into the real project, backing up what is overwritten. Never deletes."""
    check_project(task.project, ({**DEFAULT_CONFIG, **(config or {})})["projects_root"])
    plan = merge_plan(task)
    backup = task.dir / "backup"
    done = {"added": [], "modified": []}
    for rel in plan["apply"]:
        target = Path(task.project) / rel
        if target.exists():
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup / rel)
            done["modified"].append(rel)
        else:
            done["added"].append(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(task.work / rel, target)
    task.merged = {
        **done,
        "at": time.time(),
        "conflicts": plan["conflicts"],
        "blocked": plan["blocked"],
    }
    task.status = "merged"
    save(task)
    return task.merged


def undo_merge(task: Task) -> tuple[int, int]:
    """Put the project back: restore backed-up files, remove files the merge added (only if they are still the merged versions)."""
    done = task.merged or {}
    real = Path(task.project)
    restored = removed = 0
    for rel in done.get("modified", []):
        if (task.dir / "backup" / rel).is_file():
            shutil.copy2(task.dir / "backup" / rel, real / rel)
            restored += 1
    for rel in done.get("added", []):
        p = real / rel
        if p.is_file() and (task.work / rel).is_file() and _sha(p) == _sha(task.work / rel):
            p.unlink()
            removed += 1
    task.merged = {}
    task.status = "done"
    save(task)
    return restored, removed


def discard(task: Task) -> bool:
    """Delete the task's own scratch folder (never the project). Only paths under CODE_HOME are removed."""
    d = task.dir.resolve()
    if d.parent != CODE_HOME.resolve():
        raise CodeProblem("unsafe", "Refusing to remove a folder outside the coding task area.")
    shutil.rmtree(d)
    return True


# ---- persistence ----
DEFAULT_CONFIG = {
    "model": MODEL,
    "default_cap_usd": 1.5,
    "max_cap_usd": 5.0,
    "timeout_minutes": 20,
    "daily_cap_usd": 5.0,
    "projects_root": "~/dev",
    "binary": "claude",
}


def save(task: Task) -> None:
    task.dir.mkdir(parents=True, exist_ok=True)
    tmp = task.dir / "task.json.tmp"
    tmp.write_text(json.dumps(asdict(task), indent=1))
    os.replace(tmp, task.dir / "task.json")


def load(task_id: str) -> Task:
    path = CODE_HOME / task_id / "task.json"
    if not path.is_file():
        raise CodeProblem("no_task", "I don't have a coding task with that id.")
    return Task(**json.loads(path.read_text()))


def all_tasks() -> list[Task | Any]:
    out = []
    for p in sorted(CODE_HOME.glob("c*/task.json")) if CODE_HOME.is_dir() else []:
        try:
            out.append(Task(**json.loads(p.read_text())))
        except (ValueError, TypeError):
            continue
    return out


# ---- running a task: one at a time, capped in dollars and minutes ----
_RUNNING = {}
_LOCK = threading.Lock()


class Run:
    def __init__(self, task: Task) -> None:
        self.task, self.proc, self.thread = task, None, None
        self.cancelled = self.timed_out = self.over_budget = False
        self.done = threading.Event()

    def wait(self, timeout: int | None = None) -> bool:
        return self.done.wait(timeout)


def running() -> list[Any | Run]:
    with _LOCK:
        return [r for r in _RUNNING.values() if not r.done.is_set()]


def _kill(run: Run) -> None:
    proc = run.proc
    if proc is None or proc.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue


def cancel(task_id: None = None) -> bool:
    """Stop the running task (the stop hotkey and 'cancel the coding task' end up here). Returns True if one was running."""
    live = [r for r in running() if task_id in (None, r.task.id)]
    for r in live:
        r.cancelled = True
        _kill(r)
    return bool(live)


def spent_today(tracker: CostTracker) -> int | float:
    return tracker.code_today() if tracker is not None else 0.0


def start(
    task: Task,
    *,
    api_key: Any,
    tracker: CostTracker | None = None,
    config: dict[str, Any] | None = None,
    claude: Any | None = None,
    sandbox: bool = True,
    on_done: Any | None = None,
) -> Run:
    """Begin the task in the background. Raises CodeProblem when something else is running or the daily coding budget would be exceeded."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    if not api_key:
        raise CodeProblem(
            "no_key", "The coding agent needs the Anthropic API key, which isn't set."
        )
    if task.status != "staged":
        raise CodeProblem("not_staged", "That coding task has already been started.")
    with _LOCK:
        if any(not r.done.is_set() for r in _RUNNING.values()):
            raise CodeProblem(
                "busy",
                "A coding task is already running. Say cancel the coding task to stop it first.",
            )
        left = float(cfg["daily_cap_usd"]) - spent_today(tracker)
        if left < task.cap_usd:
            raise CodeProblem(
                "daily_cap",
                f"Today's coding budget has ${max(left, 0):.2f} left, which is less than this task's ${task.cap_usd:.2f} limit.",
            )
        run = Run(task)
        _RUNNING[task.id] = run
        task.status = "running"
        task.started = time.time()
        save(task)
    run.thread = threading.Thread(
        target=_execute,
        args=(run, api_key, tracker, cfg, claude or cfg["binary"], sandbox, on_done),
        daemon=True,
    )
    run.thread.start()
    return run


def _execute(
    run: Any,
    api_key: Any,
    tracker: CostTracker,
    cfg: dict[str, Any],
    claude: Any,
    sandbox: Any,
    on_done: Any,
) -> None:
    task, result, cost, rc, proxy = run.task, {}, 0.0, None, None
    try:
        count, _ = prepare(task) if not task.baseline else (0, [])
        from julius.agents import egress_proxy

        proxy = egress_proxy.EgressProxy(
            log_path=task.dir / "egress.log"
        ).start()  # Anthropic API only; see julius_egress_proxy.py
        argv, env, cwd = build_command(
            task, api_key, claude=claude, config=cfg, sandbox=sandbox, proxy=proxy
        )
        with (
            open(task.dir / "claude.err", "wb") as err,
            open(task.dir / "transcript.jsonl", "w") as transcript,
        ):
            run.proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=err,
                text=True,
                start_new_session=True,
            )
            if (
                run.cancelled
            ):  # cancel() arrived before the process existed: it had nothing to kill then
                _kill(run)

            def watchdog() -> None:
                if not run.done.wait(task.timeout_s) and run.proc.poll() is None:
                    run.timed_out = True
                    _kill(run)

            threading.Thread(target=watchdog, daemon=True).start()
            for line in run.proc.stdout:
                transcript.write(line)
                transcript.flush()
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                seen = event.get("total_cost_usd", event.get("cost_usd"))
                if isinstance(seen, (int, float)):
                    cost = max(cost, float(seen))
                    if cost > task.cap_usd * 1.25 and not run.over_budget:
                        run.over_budget = True
                        _kill(run)  # the CLI's own limit failed to stop it
                if event.get("type") == "result":
                    result = event
            rc = run.proc.wait()
            run.proc.stdout.close()
    except (
        Exception
    ) as exc:  # sandbox or launch problems must end the task cleanly, not leave it "running"
        result = {
            "is_error": True,
            "result": f"The coding task could not start: {type(exc).__name__}: {exc}",
        }
    finally:
        if proxy is not None:
            proxy.stop()
        _finish(run, result, cost, rc, tracker)
        run.done.set()
        if on_done is not None:
            with contextlib.suppress(Exception):
                on_done(task)


def _finish(run: Any, result: Any, cost: Any, rc: Any, tracker: CostTracker) -> None:
    task = run.task
    task.cost_usd = round(max(cost, float(result.get("total_cost_usd") or 0)), 4)
    task.finished = time.time()
    subtype = str(result.get("subtype", ""))
    text = " ".join(str(result.get("result") or "").split())
    if run.cancelled:
        task.status, task.stop_reason = "cancelled", "cancelled"
    elif run.timed_out:
        task.status, task.stop_reason = "timeout", f"stopped after {task.timeout_s // 60} minutes"
    elif run.over_budget or "budget" in subtype or task.cost_usd >= task.cap_usd * 0.98:
        task.status, task.stop_reason = "capped", f"reached its ${task.cap_usd:.2f} limit"
    elif result.get("is_error") or rc not in (0, None) or not result:
        task.status, task.stop_reason = "failed", text[:160] or f"the agent exited with code {rc}"
    else:
        task.status = "done"
    task.summary = text[:300]
    try:
        task.changes = summarize(task) if task.work.is_dir() else ""
    except Exception:
        task.changes = ""
    if tracker is not None and task.cost_usd:
        tracker.record_usd("code", task.cost_usd, note=f"code {task.id}")
    save(task)


def spoken_result(task: Task) -> str:
    """What Julius says when a task ends."""
    cost = f"${task.cost_usd:.2f}"
    if task.status == "done":
        return f"The coding task finished for {cost}. {task.changes}. Say show the changes, merge it, or discard it."
    if task.status == "capped":
        return f"The coding task stopped because it {task.stop_reason}, after {cost}. {task.changes or 'Nothing changed.'}. Say show the changes or discard it."
    if task.status == "timeout":
        return f"The coding task {task.stop_reason} and cost {cost}. {task.changes or 'Nothing changed.'}"
    if task.status == "cancelled":
        return f"The coding task was cancelled after {cost}."
    return f"The coding task failed after {cost}. {task.stop_reason or 'No details.'}"
