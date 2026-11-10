"""Finding, listing and reading files."""

from __future__ import annotations

import builtins
import fnmatch
import os
from pathlib import Path
from posix import DirEntry
from typing import Any

from julius.agents.file_common import (
    FileProblem,
    response,
    result,
)


class ScanMixin:
    """Finding, listing and reading files."""

    def _matches(
        self, path: str | Path, expected: dict[str, str | int], *, identity: bool = False
    ) -> bool:
        try:
            p = self.resolve(path, internal=True)
            info = p.stat()
            if info.st_size != expected.get("size"):
                return False
            if identity and (info.st_mtime_ns, info.st_dev, info.st_ino) != (
                expected.get("mtime_ns"),
                expected.get("device"),
                expected.get("inode"),
            ):
                return False
            actual = self._fingerprint(p)
            keys = ["size", "sha256"] + (["mtime_ns", "device", "inode"] if identity else [])
            return all(actual[k] == expected[k] for k in keys)
        except (OSError, FileProblem):
            return False

    def _scan(
        self,
        source: str | Path,
        pattern: str | builtins.list[str] = "*",
        recursive: bool = False,
        include_hidden: bool = False,
        limit: int | None = None,
    ) -> Any:
        p = self.resolve(source)
        if not p.is_dir():
            raise FileProblem("not_folder", f"{p.name} is not a folder.")
        patterns = pattern if isinstance(pattern, (list, tuple)) else [pattern]
        if (
            not patterns
            or len(patterns) > 32
            or any(
                not isinstance(item, str) or not item or "/" in item or "\\" in item
                for item in patterns
            )
        ):
            raise FileProblem("bad_pattern", "Use filename patterns, not paths, for the search.")
        cap = min(limit or self.MAX_RESULTS, self.MAX_RESULTS)
        hits, issues, stack = [], [], [p]
        visited, stopped = 0, False
        while stack and not stopped:
            folder = stack.pop()
            try:
                # Do not materialize/sort an entire large directory.
                with os.scandir(folder) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > self.MAX_ENTRIES:
                            stopped = True
                            break
                        self._scan_entry(
                            entry, p, patterns, recursive, include_hidden, stack, hits, issues
                        )
                        if len(hits) > cap:
                            stopped = True
                            break
            except OSError as exc:
                issues.append({"path": str(folder), "error": str(exc)})
        hits.sort(key=lambda row: row["relative_path"].casefold())
        self.last_scan = {
            "visited_entries": min(visited, self.MAX_ENTRIES),
            "truncated": stopped,
            "issues": issues[:20],
        }
        return hits[:cap], self.last_scan

    def _scan_entry(
        self,
        entry: DirEntry,
        base: Path,
        patterns: builtins.list[str],
        recursive: bool,
        include_hidden: bool,
        stack: builtins.list[Any | Path],
        hits: builtins.list[Any | dict[str, str | int]],
        issues: builtins.list[Any],
    ) -> None:
        """Queue a folder for descent or record a matching file; skip hidden, protected, symlinks."""
        if entry.path == str(self.state_dir):
            return
        if (not include_hidden and entry.name.startswith(".")) or entry.is_symlink():
            return
        try:
            if entry.is_dir(follow_symlinks=False):
                if recursive:
                    stack.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False) and any(
                fnmatch.fnmatchcase(entry.name.casefold(), item.casefold()) for item in patterns
            ):
                path = self.resolve(entry.path)
                st = path.stat()
                hits.append(
                    {
                        "path": str(path),
                        "name": path.name,
                        "relative_path": str(path.relative_to(base)),
                        "size_bytes": st.st_size,
                        "modified_ns": st.st_mtime_ns,
                    }
                )
        except OSError as exc:
            issues.append({"path": entry.path, "error": str(exc)})

    @response
    def find(
        self, pattern: str | builtins.list[str] = "*", source: str = ".", recursive: bool = False
    ) -> dict[str, Any]:
        hits, scan = self._scan(source, pattern, recursive)
        suffix = (
            " The search limit was reached; narrow the folder or filename."
            if scan["truncated"]
            else ""
        )
        if scan["issues"]:
            suffix += " Some folders could not be searched; these results are incomplete."
        return result(
            True, "found", f"Found {len(hits)} matching files." + suffix, items=hits, **scan
        )

    @response
    def list(self, directory: str = ".", limit: int = 40) -> dict[str, Any]:
        p = self.resolve(directory)
        if not p.is_dir():
            raise FileProblem("not_folder", f"{p.name} is not a folder.")
        cap = max(1, min(int(limit), 200))
        items = []
        truncated = False
        visited = 0
        protected = str(self.state_dir)
        with os.scandir(p) as entries:
            for entry in entries:
                visited += 1
                if visited > self.MAX_ENTRIES:
                    truncated = True
                    break
                if entry.path == protected:
                    continue
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                if len(items) >= cap:
                    truncated = True
                    break
                items.append(
                    {
                        "name": entry.name,
                        "path": entry.path,
                        "kind": "folder" if entry.is_dir(follow_symlinks=False) else "file",
                        "size_bytes": entry.stat(follow_symlinks=False).st_size,
                    }
                )
        items.sort(key=lambda item: (item["kind"] != "folder", item["name"].casefold()))
        return result(
            True,
            "listed",
            f"Showing {len(items)} items in {p.name}."
            + (" More items are available." if truncated else ""),
            items=items,
            truncated=truncated,
            visited_entries=min(visited, self.MAX_ENTRIES),
            path=str(p),
        )

    @response
    def read(
        self, path: str, max_chars: int = 2000
    ) -> dict[str, str | bool | dict[str, str | bool]]:
        p = self.resolve(path)
        cap = max(1, min(int(max_chars), 20000))
        if not p.is_file():
            raise FileProblem("not_file", f"{p.name} is not a file.")
        if p.suffix.casefold() in {
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".ppt",
            ".pptx",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".heic",
            ".mp3",
            ".mp4",
            ".wav",
            ".zip",
        }:
            raise FileProblem(
                "unsupported_format",
                "I cannot extract readable text from that document or media format yet.",
            )
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            raw = f.read(cap * 4 + 4)
        if b"\x00" in raw:
            raise FileProblem("binary_file", f"{p.name} is not a text document.")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            # A bounded read may cut a multibyte character; do not hide invalid interior bytes.
            import codecs

            try:
                text = codecs.getincrementaldecoder("utf-8-sig")().decode(raw, final=False)
            except UnicodeDecodeError:
                raise FileProblem(
                    "unsupported_encoding", f"{p.name} is not readable UTF-8 text."
                ) from None
        truncated = len(text) > cap or p.stat().st_size > len(raw)
        return result(
            True,
            "read",
            f"Read {p.name}." + (" Showing only the beginning." if truncated else ""),
            path=str(p),
            text=text[:cap],
            truncated=truncated,
        )
