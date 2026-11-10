"""Filesystem locations shared across the package (the repository root holds julius.toml, the local data folders and the native helpers)."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
