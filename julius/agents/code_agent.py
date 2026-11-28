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

# Running tasks as a low-privilege macOS user (threat model S1; plan in julius-orb docs/S1_M3_PLAN.md). Off unless the
# config sets code.run_as. Then the task folders live in SHARED_ROOT (yours and that user's, through a shared group),
# `claude` starts through the root-owned CODE_RUN wrapper by one narrow sudo rule, and everything Julius must be able to
# trust after the run (task.json with the project to merge into, the merge backups, the transcript, the git history it
# diffs against) lives in PRIVATE_HOME, which only you can read: the task user can rewrite anything in its own folder.
RUN_AS = ""
SHARED_ROOT = Path("/Users/Shared/julius-code")
PRIVATE_HOME = Path("~/Library/Application Support/Julius/code-private").expanduser()
CODE_RUN = "/usr/local/libexec/julius/code-run"
CODE_REAP = "/usr/local/libexec/julius/code-reap"
SUDO = "/usr/bin/sudo"
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
# What the diff (card and line counts) leaves out: caches the agent's own test runs create, and compiled files. Same set the snapshot skips.
DIFF_EXCLUDE = [f":(exclude,glob)**/{d}/**" for d in sorted(SKIP_DIRS - {".git", ".DS_Store"})] + [
    ":(exclude,glob)**/*.pyc",
    ":(exclude,glob)**/*.pyo",
    ":(exclude,glob)**/.DS_Store",
]
MAX_MERGE_BYTES = (
    20 * 1024 * 1024
)  # a file larger than this is never merged back (left in the task's copy)
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
    # found readable by the escape tests (2026-11-25): shell history and start-up files hold exported keys, and the
    # browsers, cookies and app containers hold logins and personal data
    "~/.zsh_history",
    "~/.bash_history",
    "~/.python_history",
    "~/.zshrc",
    "~/.zprofile",
    "~/.zshenv",
    "~/.bashrc",
    "~/.bash_profile",
    "~/.profile",
    "~/.zsh_sessions",
    "~/Library/Cookies",
    "~/Library/Containers",
    "~/Library/Group Containers",
    "~/Library/Accounts",
    "~/Library/Application Support/Firefox",
    "~/Library/Application Support/BraveSoftware",
    "~/Library/Application Support/Microsoft Edge",
    "~/Library/Application Support/Arc",
    "/Library/Audio",
    "/Volumes",
]
TOOLS = "Read,Edit,Write,Grep,Glob,Bash"  # under --bare (simple mode) Claude Code offers only Read, Edit and Bash
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
    "anything crypto-related, or secrets. If the task needs any of that, stop and say so. Your tools are Read, Edit and Bash (--bare runs Claude Code's "
    "simple mode: no Write tool). Change files with Edit. Create a new file, a test included, with Edit too: give the new path and an "
    "empty old_string. Never write files from Bash (multi-line commands, redirects and heredocs are refused). Run tests with "
    "python3 -m unittest (pytest is not installed). When finished, reply with at most two plain sentences: what you changed and whether the tests pass."
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

    @property
    def private(self) -> Path:
        """Where Julius keeps what it must trust after the run: the task folder itself, unless tasks run as another user."""
        return PRIVATE_HOME / self.id if RUN_AS else self.dir

    @property
    def git_dir(self) -> Path | None:
        """Julius's own record of the baseline when tasks run as another user, so it never runs git on a repo that user
        could have configured (core.fsmonitor and friends would run its code as you). None = the work folder's .git."""
        return self.private / "git" if RUN_AS else None


def configure(config: dict[str, Any] | None) -> None:
    """Apply code.run_as from the config (called once by the coding session at start)."""
    global RUN_AS, CODE_HOME
    RUN_AS = str((config or {}).get("run_as") or "").strip()
    if RUN_AS:
        CODE_HOME = SHARED_ROOT


def run_as_home(user: str | None = None) -> Path:
    import pwd

    return Path(pwd.getpwnam(user or RUN_AS).pw_dir)


def run_as_problem(user: str | None = None) -> str:
    """Why tasks can't run as the configured user yet ("" when they can). Checked before every task: a missing piece means
    the task refuses to start, never that it quietly runs as you."""
    import grp
    import pwd

    user = user or RUN_AS
    try:
        info = pwd.getpwnam(user)
    except KeyError:
        return f"the coding user {user} doesn't exist on this Mac yet"
    if info.pw_uid == os.getuid() or info.pw_uid == 0:
        return f"{user} is not a separate low-privilege user"
    with contextlib.suppress(KeyError):
        if user in grp.getgrnam("admin").gr_mem:
            return f"{user} is an administrator; the coding user must be a standard user"
    for script in (CODE_RUN, CODE_REAP):
        try:
            st = os.stat(script)
        except OSError:
            return f"{script} isn't installed"
        if st.st_uid != 0 or st.st_mode & 0o022:
            return f"{script} must be owned by root and writable by nobody else"
    if not SHARED_ROOT.is_dir():
        return f"the task folder {SHARED_ROOT} doesn't exist"
    for script in (CODE_RUN, CODE_REAP):
        # run it for real with no arguments: it prints its usage and exits 2. (`sudo -l` can't be trusted here: by
        # default it says yes to anything once any no-password rule exists for you, 2026-11-27.)
        r = subprocess.run(
            [SUDO, "-n", "-u", user, script],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
        )
        if r.returncode != 2 or "usage" not in r.stderr:
            return (
                f"the sudo rule that lets Julius run {Path(script).name} as {user} isn't installed"
            )
    return ""


def _share(path: Path) -> None:
    """Give the shared group read and write on what Julius made in a task folder (Julius's own umask is 077)."""
    for dirpath, _dirnames, filenames in os.walk(path):
        os.chmod(dirpath, 0o2770)
        for name in filenames:
            f = Path(dirpath) / name
            if not f.is_symlink():
                os.chmod(f, 0o660 | (0o110 if f.stat().st_mode & 0o100 else 0))


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


def _git(work: Path, *args, git_dir: Path | None = None) -> subprocess.CompletedProcess:
    """git in a task's work folder. With `git_dir`, against Julius's private repo for that folder instead of its .git."""
    where = ["--git-dir", str(git_dir), "--work-tree", str(work)] if git_dir else ["-C", str(work)]
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(work.parent),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }
    return subprocess.run(
        [
            "git",
            *where,
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
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
    _git(task.work, "init", "-q")  # the agent's own repo, so it can diff its work
    _git(task.work, "add", "-A")
    _git(task.work, "commit", "-q", "--allow-empty", "-m", "baseline")
    if task.git_dir:  # and Julius's, out of the task user's reach
        task.git_dir.parent.mkdir(parents=True, exist_ok=True)
        _git(task.work, "init", "-q", git_dir=getattr(task, "git_dir", None))
        _git(task.work, "add", "-A", git_dir=getattr(task, "git_dir", None))
        _git(
            task.work,
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "baseline",
            git_dir=getattr(task, "git_dir", None),
        )
        _share(task.dir)
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
    # Julius's own interpreter lives in ~/.julius/env-service: the agent may read that one folder (code and libraries, no secrets) and nothing else under ~/.julius
    prefix = Path(sys.prefix).resolve()
    if prefix.is_relative_to(home / ".julius"):
        lines.append(f'(allow file-read* (subpath "{prefix}"))')
    # other coding tasks (their copies and transcripts) and anything else in the task area: their CONTENTS are denied, but
    # not the folders on the path to this task, which Claude Code's Edit checks before writing (denying the whole area made
    # every Edit fail, live 2026-11-26)
    area = CODE_HOME.resolve()
    lines.append(f'(deny file-read* (regex #"^{re.escape(str(area))}/[^/]+/"))')
    for parent in (area.parent,):
        with contextlib.suppress(OSError):
            for child in parent.iterdir():
                if child.resolve() != area:
                    lines.append(f'(deny file-read* (subpath "{child.resolve()}"))')
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
    path = f"{Path(sys.executable).parent}:/usr/bin:/bin:/usr/sbin:/sbin"
    if RUN_AS:  # that user can't read your home, so it brings its own claude and tools
        home = run_as_home()
        claude = str(cfg.get("run_as_claude") or home / ".local" / "bin" / "claude")
        path = f"{home / '.local' / 'bin'}:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        os.chmod(profile, 0o640)
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
        "--include-partial-messages",  # running output-token counts (thinking included), so the cost cap can act mid-task
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
        "PATH": path,
        "HOME": str(task.dir / "home"),
        "TMPDIR": str(task.dir / "tmp"),
        "JULIUS_CODE_TASK": task.id,  # every process the agent starts carries it: _reap finds the ones left behind
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


def launch(
    task: Task, argv: list[str], env: dict[str, str]
) -> tuple[list[str], dict[str, str], str | None]:
    """(argv, env, stdin text) that actually start the run. As another user: sudo runs the root-owned code-run wrapper
    with the task id only; the command and its environment, API key included, go on stdin (argv shows in `ps`, and
    sudo would drop the environment). code-run refuses anything not under this task's own sandbox profile."""
    if not RUN_AS:
        return argv, env, None
    if argv[:3] != ["/usr/bin/sandbox-exec", "-f", str(task.dir / "sandbox.sb")]:
        raise CodeProblem("unsafe", "Tasks that run as the coding user must run in the sandbox.")
    spec = json.dumps({"argv": argv, "env": env})
    return [SUDO, "-n", "-u", RUN_AS, CODE_RUN, task.id], {"PATH": "/usr/bin:/bin"}, spec


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
        _git(task.work, "add", "-A", git_dir=getattr(task, "git_dir", None))
        out = _git(
            task.work,
            "diff",
            "--cached",
            "--numstat",
            "HEAD",
            "--",
            ".",
            *DIFF_EXCLUDE,
            git_dir=getattr(task, "git_dir", None),
        ).stdout
    except subprocess.CalledProcessError:
        return 0, 0
    ins = dele = 0
    for line in out.splitlines():
        a, b, *_ = line.split("\t")
        ins += int(a) if a.isdigit() else 0
        dele += int(b) if b.isdigit() else 0
    return ins, dele


def diff_lines(task: Task, limit: int = 160) -> list[str]:
    """The unified diff of the work folder against the baseline (new files included; caches, compiled and binary files left out), for the panel's code card. [] when git cannot say."""
    try:
        _git(task.work, "add", "-A", git_dir=getattr(task, "git_dir", None))
        out = _git(
            task.work,
            "diff",
            "--cached",
            "--no-color",
            "-U2",
            "HEAD",
            "--",
            ".",
            *DIFF_EXCLUDE,
            git_dir=getattr(task, "git_dir", None),
        ).stdout
    except subprocess.CalledProcessError:
        return []
    keep = [  # git headers, and binary files (no readable lines to show), are left out
        ln
        for ln in out.splitlines()
        if not ln.startswith(
            ("diff --git", "index ", "new file mode", "deleted file mode", "--- ", "Binary files ")
        )
    ]
    return keep[:limit]


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


def merge_plan(task: Task, key_values: tuple[str, ...] = ()) -> dict[str, list[Any | str]]:
    """What a merge would do, checked against the real project right now. `secrets` lists files whose contents hold a key
    (this Mac's own key values, or a gitleaks-style token pattern): any of those refuses the whole merge (threat model M2)."""
    from julius import secrets_scan

    c = changes(task)
    real = Path(task.project)
    apply, conflicts, blocked, secrets = [], [], [], []
    values = secrets_scan.known_values(key_values)
    work = task.work.resolve()
    for rel in c["added"] + c["modified"]:
        src = task.work / rel
        if (
            not safe_name(rel)
            or src.is_symlink()
            or not src.resolve().is_relative_to(work)  # a folder on the way swapped for a link
            or src.stat().st_size > MAX_MERGE_BYTES
        ):
            blocked.append(rel)
            continue
        kinds = secrets_scan.scan_file(task.work / rel, values)
        if kinds:
            secrets.append({"file": rel, "kinds": sorted(set(kinds))})
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
        "secrets": secrets,
        "deleted_not_applied": c["deleted"],
    }


def merge(task: Task, config: dict[str, str] | None = None) -> dict[str, float | list[str]]:
    """Apply the plan: copy added/modified files into the real project, backing up what is overwritten. Never deletes."""
    check_project(task.project, ({**DEFAULT_CONFIG, **(config or {})})["projects_root"])
    plan = merge_plan(task)
    if plan["secrets"]:
        listed = "; ".join(f"{s['file']} ({', '.join(s['kinds'])})" for s in plan["secrets"][:4])
        raise CodeProblem(
            "secret",
            f"I won't merge: {len(plan['secrets'])} file(s) contain what looks like a key: {listed}. "
            "Nothing was copied. Remove the key from the task's copy, or discard the task.",
        )
    backup = task.private / "backup"
    done = {"added": [], "modified": []}
    for rel in plan["apply"]:
        target = Path(task.project) / rel
        if target.exists():
            mode = target.stat().st_mode & 0o7777  # the project's file keeps its own permissions
            (backup / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup / rel)
            done["modified"].append(rel)
        else:
            mode = (
                task.work / rel
            ).stat().st_mode & 0o755 | 0o600  # never group- or world-writable
            done["added"].append(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(task.work / rel, target)
        os.chmod(target, mode)
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
        if (task.private / "backup" / rel).is_file():
            shutil.copy2(task.private / "backup" / rel, real / rel)
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
    if RUN_AS:
        _reap(
            task
        )  # nothing of the task user's left running, and its files opened up so they can be removed
        if task.private.resolve().parent == PRIVATE_HOME.resolve():
            shutil.rmtree(task.private, ignore_errors=True)
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
    task.private.mkdir(parents=True, exist_ok=True)
    tmp = task.private / "task.json.tmp"
    tmp.write_text(json.dumps(asdict(task), indent=1))
    os.replace(tmp, task.private / "task.json")


def _records() -> Path:
    return PRIVATE_HOME if RUN_AS else CODE_HOME


def load(task_id: str) -> Task:
    if not re.fullmatch(r"c[0-9a-f]{8}", str(task_id)):
        raise CodeProblem("no_task", "I don't have a coding task with that id.")
    path = _records() / task_id / "task.json"
    if not path.is_file():
        raise CodeProblem("no_task", "I don't have a coding task with that id.")
    return Task(**json.loads(path.read_text()))


def all_tasks() -> list[Task | Any]:
    out = []
    for p in sorted(_records().glob("c*/task.json")) if _records().is_dir() else []:
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


# USD per million tokens (Claude Opus 5.5: $4 in, $20 out, cache writes $5 for 5 minutes or $8 for 1 hour, cache reads $0.20).
# Unknown models are priced as Opus 5.5, the most a task here can cost per token.
CODE_RATES = {
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "write_5m": 5.0, "write_1h": 8.0, "read": 0.20},
}


def message_cost(usage: dict[str, Any], model: str = MODEL) -> float:
    """USD for one message's token usage (input, output, cache writes by lifetime, cache reads)."""
    r = CODE_RATES.get(model) or CODE_RATES["claude-opus-5-5"]
    n = lambda k: float(usage.get(k) or 0)  # noqa: E731
    split = usage.get("cache_creation") or {}
    w1h = float(split.get("ephemeral_1h_input_tokens") or 0)
    w5m = (
        float(split.get("ephemeral_5m_input_tokens") or 0)
        if split
        else n("cache_creation_input_tokens") - w1h
    )
    return (
        n("input_tokens") * r["in"]
        + n("output_tokens") * r["out"]
        + w5m * r["write_5m"]
        + w1h * r["write_1h"]
        + n("cache_read_input_tokens") * r["read"]
    ) / 1_000_000


# Estimating a message while it is written. The CLI streams text and thinking chunks but gives the output token count
# only in message_delta at the END of each message, and one Opus message can cost most of a $0.10 cap. Measured on
# 2026-11-26: text ran about 2.75 characters a token; thinking chunks carry no text, and 1,602 thinking tokens took 16 s.
CHARS_PER_TOKEN = (
    2.5  # a little under the measured 2.75, so the estimate errs toward stopping early
)
THINKING_TOKENS_PER_S = 100.0


class CostMeter:
    """The task's running cost from what the CLI streams (it runs with --include-partial-messages).

    Each message is priced once, from its largest usage so far: message_start brings the input and cache tokens,
    message_delta (at the end of the message) the final output tokens, thinking included. While a message is still being
    written its output is estimated from the text chunks and the time spent in thinking blocks, and the real count
    replaces the estimate when it arrives. The whole `assistant` events report usage as the message STARTS (a handful of
    output tokens), so on their own they undercount badly: on 2026-11-26 they showed $0.019 of a $0.149 task."""

    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.usage: dict[str, dict[str, Any]] = {}
        self.model: dict[str, str] = {}
        self.current: str | None = None
        self.open_chars = 0  # text in the message being written
        self.open_thinking_s = 0.0
        self.thinking_since: float | None = None
        self.finished: set[str] = set()
        self.wants_another_turn = (
            False  # the last message ended in a tool call, so the CLI will send another request
        )

    def _merge(self, key: str, usage: dict[str, Any], model: str | None = None) -> None:
        u = self.usage.setdefault(key, {})
        for k, v in usage.items():
            if isinstance(v, (int, float)):
                u[k] = max(u.get(k, 0), v)
            elif k == "cache_creation" and isinstance(v, dict):
                c = u.setdefault("cache_creation", {})
                for ck, cv in v.items():
                    if isinstance(cv, (int, float)):
                        c[ck] = max(c.get(ck, 0), cv)
        if model:
            self.model[key] = model

    def _close_thinking(self) -> None:
        if self.thinking_since is not None:
            self.open_thinking_s += self.clock() - self.thinking_since
            self.thinking_since = None

    def add(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        if event.get("type") == "stream_event" and isinstance(event.get("event"), dict):
            ev = event["event"]
            kind = ev.get("type")
            if kind == "message_start" and isinstance(ev.get("message"), dict):
                m = ev["message"]
                self.wants_another_turn = False
                self.current = str(m.get("id") or f"m{len(self.usage)}")
                self.open_chars, self.open_thinking_s, self.thinking_since = 0, 0.0, None
                self._merge(self.current, m.get("usage") or {}, m.get("model"))
            elif kind == "content_block_start" and isinstance(ev.get("content_block"), dict):
                self._close_thinking()
                if ev["content_block"].get("type") in ("thinking", "redacted_thinking"):
                    self.thinking_since = self.clock()
            elif kind == "content_block_delta" and isinstance(ev.get("delta"), dict):
                d = ev["delta"]
                self.open_chars += len(str(d.get("text") or d.get("partial_json") or ""))
            elif kind == "content_block_stop":
                self._close_thinking()
            elif kind == "message_delta" and isinstance(ev.get("usage"), dict):
                key = self.current or f"m{len(self.usage)}"
                self._merge(key, ev["usage"])
                self.finished.add(key)
                self.wants_another_turn = (ev.get("delta") or {}).get("stop_reason") == "tool_use"
            elif kind == "message_stop" and self.current:
                self._close_thinking()
                self.finished.add(self.current)
            return
        msg = event.get("message")
        if (
            event.get("type") == "assistant"
            and isinstance(msg, dict)
            and isinstance(msg.get("usage"), dict)
        ):
            self._merge(str(msg.get("id") or f"m{len(self.usage)}"), msg["usage"], msg.get("model"))

    def _estimated_output(self) -> int:
        thinking_s = self.open_thinking_s
        if self.thinking_since is not None:
            thinking_s += self.clock() - self.thinking_since
        return int(self.open_chars / CHARS_PER_TOKEN + thinking_s * THINKING_TOKENS_PER_S)

    @property
    def next_turn_usd(self) -> float:
        """What the next request's input will cost at least: the last message's input again (the context only grows).
        It is billed when the request is SENT, before any of its output streams back, so Julius stops between turns
        when it would cross the cap. 0 unless the last message ended in a tool call (another request coming)."""
        if not (self.wants_another_turn and self.current in self.finished):
            return 0.0
        u = {k: v for k, v in self.usage.get(self.current, {}).items() if k != "output_tokens"}
        return message_cost(u, self.model.get(self.current) or MODEL)

    @property
    def usd(self) -> float:
        total = 0.0
        for key, u in self.usage.items():
            if key == self.current and key not in self.finished:
                u = {**u, "output_tokens": max(u.get("output_tokens", 0), self._estimated_output())}
            total += message_cost(u, self.model.get(key) or MODEL)
        return round(total, 6)


def _reap(task: Task) -> list[int]:
    """Kill what the agent left running: a child that called setsid has left the task's process group (so killpg misses
    it) and keeps the output pipe open, so the task never ends. Found by working directory inside the task folder, or by
    the JULIUS_CODE_TASK marker in its environment (both only for this user's processes). Returns the pids killed.
    As another user, the code-reap wrapper does it in that user's name, and ends EVERY process of that user (only one task
    runs at a time and nothing else runs as it, so a process that shed both its folder and its marker can't survive)."""
    if RUN_AS:
        out = subprocess.run(
            [SUDO, "-n", "-u", RUN_AS, CODE_REAP, task.id, "--all"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
        with contextlib.suppress(ValueError):
            return sorted(int(x) for x in json.loads(out or "{}").get("killed", []))
        return []
    me, uid, root = os.getpid(), os.getuid(), str(task.dir.resolve())
    pids = set()
    with contextlib.suppress(Exception):
        out = subprocess.run(
            ["/usr/sbin/lsof", "-nP", "-a", "-u", str(uid), "-d", "cwd", "-Fpn"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        pid = None
        for line in out.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            elif (
                line.startswith("n")
                and pid
                and (line[1:] == root or line[1:].startswith(root + "/"))
            ):
                pids.add(pid)
    with contextlib.suppress(Exception):
        out = subprocess.run(
            ["/bin/ps", "-Eww", "-u", str(uid), "-o", "pid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        for line in out.splitlines():
            if f"JULIUS_CODE_TASK={task.id}" in line:
                pids.add(int(line.split(None, 1)[0]))
    pids.discard(me)
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
    return sorted(pids)


def _kill(run: Run, hard: bool = False) -> None:
    """Stop the task's process group: SIGTERM with 3 s grace, then SIGKILL. `hard` skips the grace. The budget stop
    uses it, because the CLI ignores SIGTERM long enough to keep generating (2026-11-26: $0.012 more on a $0.10 cap)."""
    proc = run.proc
    if proc is None or proc.poll() is not None:
        _reap(run.task)
        return
    if RUN_AS:  # the process is sudo's, which you can't signal: the task user's wrapper ends the task's processes
        _reap(run.task)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)
        return
    for sig in (signal.SIGKILL,) if hard else (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=3)
            _reap(run.task)
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
    if RUN_AS:
        problem = run_as_problem()
        if problem:
            raise CodeProblem(
                "run_as",
                f"I won't start the coding task: {problem}. It never runs as you instead. Finish the setup, or clear code.run_as.",
            )
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
    meter = CostMeter()
    try:
        count, _ = prepare(task) if not task.baseline else (0, [])
        from julius.agents import egress_proxy

        proxy = egress_proxy.EgressProxy(
            log_path=task.private / "egress.log"
        ).start()  # Anthropic API only; see julius_egress_proxy.py
        argv, env, cwd = build_command(
            task, api_key, claude=claude, config=cfg, sandbox=sandbox, proxy=proxy
        )
        argv, env, spec = launch(task, argv, env)
        with (
            open(task.private / "claude.err", "wb") as err,
            open(task.private / "transcript.jsonl", "w") as transcript,
        ):
            run.proc = subprocess.Popen(
                argv,
                cwd=cwd if spec is None else "/",
                env=env,
                stdin=subprocess.DEVNULL if spec is None else subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=err,
                text=True,
                start_new_session=True,
            )
            if spec is not None:
                run.proc.stdin.write(spec)
                run.proc.stdin.close()
            if (
                run.cancelled
            ):  # cancel() arrived before the process existed: it had nothing to kill then
                _kill(run)

            def watchdog() -> None:
                if not run.done.wait(task.timeout_s) and run.proc.poll() is None:
                    run.timed_out = True
                    _kill(run)

            threading.Thread(target=watchdog, daemon=True).start()

            def reaper() -> (
                None
            ):  # the agent itself exited: end anything it left behind, so the output pipe closes
                run.proc.wait()
                _reap(task)

            threading.Thread(target=reaper, daemon=True).start()
            for line in run.proc.stdout:
                transcript.write(line)
                transcript.flush()
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                meter.add(event)  # each streamed message priced from its token usage, as it arrives
                seen = event.get("total_cost_usd", event.get("cost_usd"))
                if isinstance(seen, (int, float)):
                    cost = max(cost, float(seen))
                cost = max(cost, meter.usd)
                # at the cap, or between turns when the next request's input alone would cross it
                at_cap = cost >= task.cap_usd or cost + meter.next_turn_usd >= task.cap_usd
                if at_cap and not run.over_budget and event.get("type") != "result":
                    run.over_budget = True
                    _kill(
                        run, hard=True
                    )  # now, not at the CLI's own total, which comes only at the end
                if event.get("type") == "result":
                    result = event
            rc = run.proc.wait()
            run.proc.stdout.close()
            _reap(task)
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
