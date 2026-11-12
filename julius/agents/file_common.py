"""Shared pieces of the file agent: its error type, reply builder, folder rules and size/summary helpers."""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any

from julius.agents.result import AgentResult


class FileProblem(Exception):
    def __init__(self, code: str, message: str, **data) -> None:
        super().__init__(message)
        self.code, self.data = code, data


MOVELIKE = frozenset(
    {"move", "rename", "trash"}
)  # operations that relocate one file and are undone by moving it back


GB = 1024**3


TRASH_NOTICE_BYTES = 1 * GB


MUSIC_EXT = frozenset(
    [
        ".logicx",
        ".band",
        ".wav",
        ".wave",
        ".aif",
        ".aiff",
        ".aifc",
        ".caf",
        ".mp3",
        ".m4a",
        ".aac",
        ".flac",
        ".ogg",
        ".opus",
        ".wma",
        ".mid",
        ".midi",
        ".als",
        ".ptx",
        ".rpp",
        ".cpr",
        ".flp",
        ".nki",
        ".nkm",
        ".nkx",
        ".nkc",
        ".ncw",
        ".nmsv",
        ".exs",
        ".sf2",
        ".sfz",
        ".fxp",
        ".fxb",
        ".vstpreset",
        ".component",
        ".vst",
        ".vst3",
        ".aaxplugin",
        ".rex",
        ".rx2",
        ".adg",
        ".adv",
        ".alc",
    ]
)


MUSIC_NAME = re.compile(
    r"(?<![a-z])(?:logic|garage[ _-]?band|waves|native[ _-]?instruments|kontakt|maschine|komplete|slate|zebra|stems?|ni)(?![a-z])",
    re.I,
)


CODE_EXT = frozenset(
    [
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".swift",
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".m",
        ".mm",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".rb",
        ".php",
        ".sh",
        ".zsh",
        ".ipynb",
        ".sql",
        ".r",
        ".cs",
        ".lua",
        ".pl",
        ".scala",
        ".toml",
        ".yaml",
        ".yml",
        ".gradle",
        ".cmake",
        ".xcodeproj",
        ".xcworkspace",
    ]
)


PROJECT_MARKERS = frozenset(
    [
        ".git",
        "package.json",
        "pyproject.toml",
        "setup.py",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "CMakeLists.txt",
        "pom.xml",
        "build.gradle",
        "Package.swift",
        "Gemfile",
    ]
)


INSTALLER_EXT = frozenset(
    [".dmg", ".pkg", ".mpkg", ".iso", ".exe", ".msi", ".appimage", ".deb", ".rpm"]
)


BUNDLE_EXT = (
    ".app",
    ".framework",
    ".bundle",
    ".kext",
    ".plugin",
    ".xpc",
    ".xcodeproj",
    ".xcworkspace",
    ".logicx",
    ".band",
)


RECENT_SECONDS = 7 * 86400


INSTALLER_SECONDS = 30 * 86400


def human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} bytes" if unit == "bytes" else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1024


def summarize_ops(ops: list[Any], limit: int = 3) -> str:
    """'a.pdf, b.txt and c.csv (12 MB in total) and 9 more' for speech; the full list is printed from the plan."""
    names = [Path(o["to"]).name for o in ops]
    total = sum(o["fingerprint"]["size"] for o in ops)
    shown = names[:limit]
    text = ", ".join(shown[:-1]) + (" and " if len(shown) > 1 else "") + shown[-1] if shown else ""
    more = len(names) - len(shown)
    return f"{text}{f' and {more} more' if more > 0 else ''} ({human_size(total)} in total)"


def result(ok: bool, code: str, message: str, **data: Any) -> AgentResult:
    return {"agent": "files", "ok": ok, "code": code, "message": message, "data": data}


_panel_sink: Callable[[AgentResult], None] | None = None


def set_panel_sink(fn: Callable[[AgentResult], None] | None) -> None:
    """The side panel registers here; each file-agent reply is handed to it (it never raises into the turn)."""
    global _panel_sink
    _panel_sink = fn


def _to_panel(out: Any) -> Any:
    if _panel_sink is not None and isinstance(out, dict):
        with contextlib.suppress(Exception):
            _panel_sink(out)
    return out


def response(method: Callable) -> Callable:
    @wraps(method)
    def wrapped(self, *args, **kwargs) -> Any:
        try:
            return _to_panel(method(self, *args, **kwargs))
        except FileProblem as exc:
            return _to_panel(result(False, exc.code, str(exc), **exc.data))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return result(False, "io_error", f"I couldn't complete that file request: {exc}")

    return wrapped


_PLAN_STATUSES = {
    "pending",
    "cancelled",
    "running",
    "partial",
    "completed",
    "undo_partial",
    "undone",
    "purged",
}
