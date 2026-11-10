"""Small persistent state that must survive a restart: quiet mode (detector off until you wake Julius by hotkey)."""

import os
from pathlib import Path


def state_dir() -> Path:
    return Path(os.environ.get("JULIUS_STATE_DIR", "~/.julius")).expanduser()


def tmp_dir() -> Path:
    """Runtime scratch space that macOS temp cleaning does not touch (the system temp dir lost the speech worker's folder mid-session once)."""
    d = state_dir() / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _flag() -> Path:
    return Path(os.environ.get("JULIUS_STATE_DIR", "~/.julius")).expanduser() / "quiet"


def is_quiet() -> bool:
    return _flag().exists()


def set_quiet(on: bool) -> None:
    f = _flag()
    if on:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("1")
    elif f.exists():
        f.unlink()
