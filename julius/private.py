"""Owner-only files (threat model L1): memory, conversations, the cost ledger, logs, tokens and recordings are readable by
you alone, not by other accounts on the Mac.

- `owner_only_umask()` makes every file Julius creates 0600 and every folder 0700. It runs first thing at start-up
  (`python -m julius`), and the LaunchAgents set the same `Umask` for the logs launchd itself creates.
- `secure_existing()` removes group and other access from what earlier versions created. It keeps your own access as it
  was (an executable stays executable) and never follows a symlink. It walks only Julius's data folders, not the Python
  environments under ~/.julius."""

from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path

UMASK = 0o077
SKIP = {
    "env-service",
    "bin",
    ".venv",
    "venv",
}  # interpreters and tools: owner-only too, but not walked file by file at every start


def owner_only_umask() -> int:
    """Set the process umask to 077; returns the previous one."""
    return os.umask(UMASK)


def data_roots() -> list[Path]:
    """Julius's own data: state (~/.julius), memory (julius_memory), logs, saved cards."""
    from julius import state
    from julius.log import LOG_DIR
    from julius.memory import sessions as jm

    return [state.state_dir(), Path(jm.MEM), LOG_DIR]


def safe_root(root: Path) -> bool:
    """Only a data folder: never the home folder, a filesystem root, the current folder or a source checkout (an empty
    JULIUS_STATE_DIR once resolved to the repo itself)."""
    r = Path(os.path.abspath(root))
    if not root.is_absolute() or str(root) in ("", "."):
        return False
    if r in (Path.home(), Path("/"), Path(os.path.abspath("."))) or len(r.parts) < 3:
        return False
    return not (r / ".git").exists() and not (r / "pyproject.toml").exists()


def tighten(path: Path) -> bool:
    """Drop group and other permissions from one path (never through a symlink). True when it changed."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return False
    mode = stat.S_IMODE(st.st_mode)
    if not mode & 0o077:
        return False
    with contextlib.suppress(OSError):
        os.chmod(path, mode & 0o700, follow_symlinks=False)
        return True
    return False


def secure_existing(roots: list[Path] | None = None, max_files: int = 20000) -> int:
    """Owner-only for every file and folder under the data roots. Returns how many were changed."""
    changed = 0
    seen = 0
    for root in roots if roots is not None else data_roots():
        root = Path(root)
        if not root.exists() or root.is_symlink() or not safe_root(root):
            continue
        changed += tighten(root)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in SKIP]
            for name in dirnames + filenames:
                seen += 1
                if seen > max_files:
                    return changed
                changed += tighten(Path(dirpath) / name)
    return changed
