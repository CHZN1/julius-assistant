"""Resolving spoken or typed paths to real ones inside the folders the agent may touch."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from julius.agents.file_common import (
    FileProblem,
)


class PathsMixin:
    """Resolving spoken or typed paths to real ones inside the folders the agent may touch."""

    def _modes(self, folders: dict[str, str] | None, allowed_roots: None) -> dict[Path, str]:
        """{folder: 'write'|'read'|'none'} from the config table; names are relative to the home folder, other entries are paths."""
        if allowed_roots is not None:
            return {Path(p).resolve(): "write" for p in allowed_roots}
        table = (
            folders
            if folders is not None
            else {"Documents": "write", "Desktop": "write", "Downloads": "write"}
        )
        modes = {}
        for name, mode in table.items():
            mode = str(mode).strip().lower()
            if mode not in ("write", "read", "none"):
                raise FileProblem(
                    "bad_config", f'[files.folders] {name} must be "write", "read" or "none".'
                )
            path = Path(os.path.expanduser(str(name)))
            path = (path if path.is_absolute() else self.root / path).resolve()
            modes[path] = mode
        return modes

    def mode_of(self, path: str | Path) -> tuple[Path, str]:
        """The permission of the most specific configured folder containing `path`."""
        p = Path(path)
        best = None
        for root, mode in self.modes.items():
            if p.is_relative_to(root) and (best is None or len(root.parts) > len(best[0].parts)):
                best = (root, mode)
        return best

    def require_write(self, path: str | Path) -> None:
        found = self.mode_of(path)
        if found is None or found[1] != "write":
            name = found[0].name if found else Path(path).parent.name
            raise FileProblem(
                "read_only",
                f"{name} is read-only for Julius, so I will not change anything there. You can change this in the [files.folders] section of julius.toml.",
            )

    def _extra_folder(self, p: Path) -> Path:
        """A configured folder outside Documents/Desktop/Downloads named anywhere in a relative path ("sandbox", "sand box/a.txt", "Documents/dev/Sandbox") -> that folder plus the rest."""
        standard = {self.root / n for n in ("Documents", "Desktop", "Downloads")}

        def key(x: Any) -> Any:
            return re.sub(r"[^a-z0-9]", "", str(x).casefold())

        extras = {
            key(f.name): f
            for f, mode in self.modes.items()
            if mode != "none" and f not in standard and f.is_dir()
        }
        words = list(p.parts)
        for i in range(len(words)):
            for j in (i + 2, i + 1):  # "sand box" may arrive as two path words
                if j <= len(words) and key("".join(words[i:j])) in extras:
                    return extras[key("".join(words[i:j]))].joinpath(*words[j:])
        return None

    def resolve(
        self, value: str | Path = ".", *, new: bool = False, internal: bool = False
    ) -> Path:
        """Turn a spoken or typed path into a checked absolute path inside the allowed folders."""
        raw = str(value).strip().strip('"').strip("'")
        if not raw:
            raise FileProblem("missing_path", "Please name the file or folder.")
        raw = os.path.expanduser(raw)
        p = Path(raw)
        if not p.is_absolute():
            p = self._anchor(raw, p)
        # Check lexical and resolved containment, and reject symlink components.
        absolute = Path(os.path.abspath(p))
        self._reject_symlinks(absolute)
        resolved = absolute.resolve()
        in_trash = absolute.is_relative_to(self.trash_dir) and resolved.is_relative_to(
            self.trash_dir
        )
        if not in_trash and not any(
            absolute.is_relative_to(a) and resolved.is_relative_to(a) for a in self.allowed
        ):
            raise FileProblem(
                "outside_scope",
                "That path is outside the folders Julius is allowed to use.",
                allowed=[str(a) for a in self.allowed],
            )
        if resolved.is_relative_to(self.state_dir):
            raise FileProblem(
                "protected_path",
                "Julius file history is protected. Use the file-plan history interface to inspect it.",
            )
        if not new and not resolved.exists():
            raise FileProblem("not_found", f"I could not find {raw}.")
        if in_trash and not internal:
            raise FileProblem(
                "protected_path",
                "The trash is managed with undo and the empty-the-trash command, not by path.",
            )
        return resolved

    def _anchor(self, raw: str, p: Path) -> Path:
        """Place a relative path under Documents, Desktop, Downloads or a configured folder."""
        if raw in {".", "here", "Documents"}:
            return self.root / "Documents"
        if p.parts[0].casefold() in {"documents", "desktop", "downloads"}:
            names = {"documents": "Documents", "desktop": "Desktop", "downloads": "Downloads"}
            rest = Path(*p.parts[1:])
            p = self.root / names[p.parts[0].casefold()] / rest
            if (
                rest.parts
                and not (p.exists() or p.is_symlink())
                and self._extra_folder(rest) is not None
            ):
                p = self._extra_folder(rest)  # "Documents/dev/Sandbox": a guessed path
            return p
        if self._extra_folder(p) is not None:
            return self._extra_folder(p)
        options = [self.root / "Documents" / p, self.root / p]
        existing = [q for q in options if q.exists() or q.is_symlink()]
        if len(existing) > 1 and existing[0] != existing[1]:
            raise FileProblem(
                "ambiguous_path",
                "More than one path matches. Please use the full folder name.",
                choices=[str(q) for q in existing],
            )
        return existing[0] if existing else options[0]

    def _reject_symlinks(self, absolute: Path) -> None:
        for part in [absolute, *absolute.parents]:
            if part.is_symlink():
                raise FileProblem(
                    "symlink",
                    "Symbolic links are excluded from file operations. Please use the actual file path.",
                )
            if part == self.root:
                break
