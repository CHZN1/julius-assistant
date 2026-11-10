"""Scoped file service with UI-ready results and durable, reversible plans.

No model calls. Mutations require a confirmed, current-session preview.
"""

from __future__ import annotations

import contextlib
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import time
import uuid
from functools import wraps
from pathlib import Path

from julius.paths import REPO_ROOT


class FileProblem(Exception):
    def __init__(self, code, message, **data):
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


def human_size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n} bytes" if unit == "bytes" else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1024


def summarize_ops(ops, limit=3):
    """'a.pdf, b.txt and c.csv (12 MB in total) and 9 more' for speech; the full list is printed from the plan."""
    names = [Path(o["to"]).name for o in ops]
    total = sum(o["fingerprint"]["size"] for o in ops)
    shown = names[:limit]
    text = ", ".join(shown[:-1]) + (" and " if len(shown) > 1 else "") + shown[-1] if shown else ""
    more = len(names) - len(shown)
    return f"{text}{f' and {more} more' if more > 0 else ''} ({human_size(total)} in total)"


def result(ok, code, message, **data):
    return {"agent": "files", "ok": ok, "code": code, "message": message, "data": data}


def response(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except FileProblem as exc:
            return result(False, exc.code, str(exc), **exc.data)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return result(False, "io_error", f"I couldn't complete that file request: {exc}")

    return wrapped


class FileAgent:
    CAPABILITIES = (
        "find",
        "list",
        "read",
        "move",
        "copy",
        "rename",
        "create",
        "trash",
        "empty_trash",
        "history",
        "preview",
        "cancel",
        "undo",
    )
    MAX_ENTRIES = 10000
    MAX_RESULTS = 200
    MAX_PLAN_FILES = 100
    MAX_PLAN_BYTES = 512 * 1024 * 1024
    PLAN_TTL = 15 * 60

    def __init__(self, root=None, state_dir=None, allowed_roots=None, trash_dir=None, folders=None):
        self.root = Path(root or Path.home()).resolve()
        self.modes = self._modes(folders, allowed_roots)
        self.allowed = tuple(p for p, mode in self.modes.items() if mode != "none")
        self.state_dir = Path(state_dir or REPO_ROOT / "julius_memory").resolve()
        self.use_birthtime = True
        self.trash_dir = Path(trash_dir or self.root / ".julius" / "trash").resolve()
        self.state_path = self.state_dir / "file_agent_state.json"
        self.session = uuid.uuid4().hex
        self._mutex = threading.RLock()
        self.last_scan = {}

    def _modes(self, folders, allowed_roots):
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

    def mode_of(self, path):
        """The permission of the most specific configured folder containing `path`."""
        p = Path(path)
        best = None
        for root, mode in self.modes.items():
            if p.is_relative_to(root) and (best is None or len(root.parts) > len(best[0].parts)):
                best = (root, mode)
        return best

    def require_write(self, path):
        found = self.mode_of(path)
        if found is None or found[1] != "write":
            name = found[0].name if found else Path(path).parent.name
            raise FileProblem(
                "read_only",
                f"{name} is read-only for Julius, so I will not change anything there. You can change this in the [files.folders] section of julius.toml.",
            )

    def _extra_folder(self, p):
        """A configured folder outside Documents/Desktop/Downloads named anywhere in a relative path ("sandbox", "sand box/a.txt", "Documents/dev/Sandbox") -> that folder plus the rest."""
        standard = {self.root / n for n in ("Documents", "Desktop", "Downloads")}
        key = lambda x: re.sub(r"[^a-z0-9]", "", str(x).casefold())
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

    def resolve(self, value=".", *, new=False, internal=False):
        raw = str(value).strip().strip('"').strip("'")
        if not raw:
            raise FileProblem("missing_path", "Please name the file or folder.")
        raw = os.path.expanduser(raw)
        p = Path(raw)
        if not p.is_absolute():
            if raw in {".", "here", "Documents"}:
                p = self.root / "Documents"
            elif p.parts[0].casefold() in {"documents", "desktop", "downloads"}:
                names = {"documents": "Documents", "desktop": "Desktop", "downloads": "Downloads"}
                rest = Path(*p.parts[1:])
                p = self.root / names[p.parts[0].casefold()] / rest
                if (
                    rest.parts
                    and not (p.exists() or p.is_symlink())
                    and self._extra_folder(rest) is not None
                ):
                    p = self._extra_folder(
                        rest
                    )  # "Documents/dev/Sandbox": a guessed path to a configured folder
            elif self._extra_folder(p) is not None:
                p = self._extra_folder(p)
            else:
                options = [self.root / "Documents" / p, self.root / p]
                existing = [q for q in options if q.exists() or q.is_symlink()]
                if len(existing) > 1 and existing[0] != existing[1]:
                    raise FileProblem(
                        "ambiguous_path",
                        "More than one path matches. Please use the full folder name.",
                        choices=[str(q) for q in existing],
                    )
                p = existing[0] if existing else options[0]
        # Check lexical and resolved containment, and reject symlink components.
        absolute = Path(os.path.abspath(p))
        for part in [absolute, *absolute.parents]:
            if part.is_symlink():
                raise FileProblem(
                    "symlink",
                    "Symbolic links are excluded from file operations. Please use the actual file path.",
                )
            if part == self.root:
                break
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

    @contextlib.contextmanager
    def _locked(self):
        with self._mutex:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with (self.state_dir / "file_agent.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def _load(self):
        if not self.state_path.exists():
            return {"version": 2, "plans": {}, "batches": []}
        if self.state_path.stat().st_size > 16 * 1024 * 1024:
            raise FileProblem(
                "history_full",
                "File history has reached its size limit. Archive it before making more changes.",
            )
        data = json.loads(self.state_path.read_text())
        if not isinstance(data, dict) or data.get("version") != 2:
            raise FileProblem(
                "state_version", "File history needs review before more changes can run."
            )
        if not isinstance(data.get("plans"), dict) or not isinstance(data.get("batches"), list):
            raise FileProblem(
                "state_corrupt", "The file history is incomplete. It was left unchanged for review."
            )
        for pid, plan in data["plans"].items():
            if (
                not isinstance(plan, dict)
                or plan.get("id") != pid
                or not isinstance(plan.get("ops"), list)
                or not plan["ops"]
                or not isinstance(plan.get("created"), (int, float))
                or not isinstance(plan.get("turn"), int)
                or not isinstance(plan.get("session"), str)
                or plan.get("action") not in {"move", "copy", "rename", "create", "trash", "purge"}
                or plan.get("status")
                not in {
                    "pending",
                    "cancelled",
                    "running",
                    "partial",
                    "completed",
                    "undo_partial",
                    "undone",
                    "purged",
                }
            ):
                raise FileProblem(
                    "state_corrupt",
                    "A file-history record is invalid. It was left unchanged for review.",
                )
            for op in plan["ops"]:
                if (
                    not isinstance(op, dict)
                    or not isinstance(op.get("to"), str)
                    or "from" not in op
                    or not isinstance(op.get("fingerprint"), dict)
                    or not {"size", "sha256"} <= op["fingerprint"].keys()
                ):
                    raise FileProblem(
                        "state_corrupt",
                        "An operation record is invalid. It was left unchanged for review.",
                    )
                if plan["action"] in MOVELIKE | {"copy"} and (
                    not isinstance(op["from"], str)
                    or not {"mtime_ns", "device", "inode"} <= op["fingerprint"].keys()
                ):
                    raise FileProblem(
                        "state_corrupt",
                        "A source-file record is invalid. It was left unchanged for review.",
                    )
                if (
                    plan["action"] == "create"
                    and plan["status"] == "pending"
                    and not isinstance(op.get("content"), str)
                ):
                    raise FileProblem(
                        "state_corrupt",
                        "The new-file preview is missing its content. It was left unchanged for review.",
                    )
        if any(pid not in data["plans"] for pid in data["batches"]):
            raise FileProblem(
                "state_corrupt",
                "The file history refers to a missing plan. It was left unchanged for review.",
            )
        return data

    def _save(self, data):
        fd, name = tempfile.mkstemp(prefix=".file-state-", dir=self.state_dir)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, self.state_path)
            directory = os.open(self.state_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _fingerprint(self, path):
        p = self.resolve(path, internal=True)
        before = p.stat()
        if not stat.S_ISREG(before.st_mode):
            raise FileProblem("not_file", f"{p.name} is not a regular file.")
        h = hashlib.sha256()
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            opened = os.fstat(f.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise FileProblem("changed", f"{p.name} changed before it could be checked.")
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        after = p.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise FileProblem("changed", f"{p.name} changed while being checked. Please try again.")
        return {
            "size": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "device": after.st_dev,
            "inode": after.st_ino,
            "sha256": h.hexdigest(),
        }

    def _matches(self, path, expected, *, identity=False):
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

    def _scan(self, source, pattern="*", recursive=False, include_hidden=False, limit=None):
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
        hits, visited, issues, stack, stopped = [], 0, [], [p], False
        protected = str(self.state_dir)
        while stack:
            folder = stack.pop()
            try:
                # Do not materialize/sort an entire large directory.
                with os.scandir(folder) as entries:
                    for entry in entries:
                        visited += 1
                        if visited > self.MAX_ENTRIES:
                            stopped = True
                            break
                        if entry.path == protected:
                            continue
                        if (
                            not include_hidden and entry.name.startswith(".")
                        ) or entry.is_symlink():
                            continue
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if recursive:
                                    stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False) and any(
                                fnmatch.fnmatchcase(entry.name.casefold(), item.casefold())
                                for item in patterns
                            ):
                                path = self.resolve(entry.path)
                                s = path.stat()
                                hits.append(
                                    {
                                        "path": str(path),
                                        "name": path.name,
                                        "relative_path": str(path.relative_to(p)),
                                        "size_bytes": s.st_size,
                                        "modified_ns": s.st_mtime_ns,
                                    }
                                )
                                if len(hits) > cap:
                                    stopped = True
                                    break
                        except OSError as exc:
                            issues.append({"path": entry.path, "error": str(exc)})
            except OSError as exc:
                issues.append({"path": str(folder), "error": str(exc)})
            if stopped:
                break
        hits.sort(key=lambda row: row["relative_path"].casefold())
        self.last_scan = {
            "visited_entries": min(visited, self.MAX_ENTRIES),
            "truncated": stopped,
            "issues": issues[:20],
        }
        return hits[:cap], self.last_scan

    @response
    def find(self, pattern="*", source=".", recursive=False):
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
    def list(self, directory=".", limit=40):
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
    def read(self, path, max_chars=2000):
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
                raise FileProblem("unsupported_encoding", f"{p.name} is not readable UTF-8 text.")
        truncated = len(text) > cap or p.stat().st_size > len(raw)
        return result(
            True,
            "read",
            f"Read {p.name}." + (" Showing only the beginning." if truncated else ""),
            path=str(p),
            text=text[:cap],
            truncated=truncated,
        )

    @response
    def propose(
        self,
        action,
        *,
        source=".",
        destination=None,
        pattern="*",
        recursive=False,
        path=None,
        content=None,
        turn=0,
        paths=None,
        origin="human",
    ):
        if action == "trash":
            return self._propose_trash(source, pattern, paths, origin, turn)
        if action not in {"move", "copy", "rename", "create"}:
            raise FileProblem("unsupported_action", "That file action is not supported.")
        if not destination:
            raise FileProblem("missing_destination", "Which folder or filename should I use?")
        dst = self.resolve(destination, new=True)
        self.require_write(dst)
        if action in {"move", "copy"}:
            if dst.exists() and not dst.is_dir():
                raise FileProblem("not_folder", f"{dst.name} is not a folder.")
            src = self.resolve(source)
            if action == "move":
                self.require_write(src)
            hits, scan = self._scan(src, pattern, recursive, limit=self.MAX_PLAN_FILES)
            if scan["truncated"] or scan["issues"]:
                raise FileProblem(
                    "incomplete_selection",
                    "I could not check the complete selection. Narrow the search before changing files.",
                    **scan,
                )
            if not hits:
                raise FileProblem(
                    "no_matches", "No matching files were found. Nothing will change."
                )
            ops = []
            for hit in hits:
                old = Path(hit["path"])
                if old.is_relative_to(dst):
                    continue
                # Preserve subfolders when a recursive selection is requested.
                target = dst / old.relative_to(src) if recursive else dst / old.name
                if old == target:
                    continue
                ops.append({"from": str(old), "to": str(target)})
        elif action == "rename":
            src = self.resolve(path)
            self.require_write(src)
            if dst.parent != src.parent:
                raise FileProblem(
                    "rename_location",
                    "A rename must keep the file in its current folder. Use move to change folders.",
                )
            if src == dst:
                raise FileProblem("no_change", "That file already has the requested name.")
            ops = [{"from": str(src), "to": str(dst)}]
        else:
            if not isinstance(content, str):
                raise FileProblem(
                    "missing_content", "Please provide the exact text to put in the new file."
                )
            if len(content.encode("utf-8")) > 1024 * 1024:
                raise FileProblem("too_large", "New text files are limited to 1 MB per request.")
            ops = [{"from": None, "to": str(dst), "content": content}]
        if not ops:
            raise FileProblem("no_change", "The selected files are already in that location.")
        total = 0
        for op in ops:
            target = self.resolve(op["to"], new=True, internal=True)
            if target.exists():
                raise FileProblem(
                    "collision",
                    f"{target.name} already exists. Choose another destination; nothing will be overwritten.",
                    path=str(target),
                )
            if op["from"] and total + Path(op["from"]).stat().st_size > self.MAX_PLAN_BYTES:
                raise FileProblem(
                    "too_large",
                    "This selection exceeds the 512 MB batch limit. Split it into smaller requests.",
                )
            op["fingerprint"] = (
                self._fingerprint(op["from"])
                if op["from"]
                else {
                    "size": len(content.encode("utf-8")),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                }
            )
            total += op["fingerprint"]["size"]
            op["status"] = "planned"
        if total > self.MAX_PLAN_BYTES:
            raise FileProblem(
                "too_large",
                "This selection exceeds the 512 MB batch limit. Split it into smaller requests.",
            )
        plan = {
            "id": uuid.uuid4().hex[:12],
            "session": self.session,
            "turn": turn,
            "created": time.time(),
            "action": action,
            "status": "pending",
            "ops": ops,
            "total_bytes": total,
            "count": len(ops),
        }
        with self._locked():
            state = self._load()
            state["plans"][plan["id"]] = plan
            self._save(state)
        detail = (
            " It covers " + summarize_ops(ops) + "." if action in {"move", "copy", "rename"} else ""
        )
        if action in {"move", "copy"}:
            message = (
                f"I found {len(ops)} file(s) in {src.name}. I can {action} them to {dst.name}."
                + detail
            )
        elif action == "rename":
            message = f"I can rename {src.name} to {dst.name}."
        else:
            message = f"I can create {dst.name} with the supplied text."
        return result(
            True,
            "preview",
            message + " Nothing has changed. Say yes to confirm or cancel to discard.",
            plan=plan,
        )

    # ---- trash: recoverable deletion ------------------------------------------------------------------------------
    def protection(self, path, origin="human"):
        """Why this file must not be moved to the trash, or None. Music, app bundles and project folders are protected for everyone; the planner
        (origin='planner') also never proposes anything touched in the last 7 days, installers used in the last 30 days, or source-code files."""
        p = Path(path)
        name = p.name.casefold()
        ext = p.suffix.casefold()
        base = next((a for a in self.allowed if p.is_relative_to(a)), self.root)
        parts = p.relative_to(base).parts if p.is_relative_to(base) else p.parts
        if p.is_symlink():
            return "it is a link"
        if not p.is_file():
            return "it is a folder, not a loose file"
        if any(part.casefold().endswith(BUNDLE_EXT) for part in parts):
            return "it is part of an app or package bundle"
        if ext in MUSIC_EXT or any(MUSIC_NAME.search(part) for part in parts):
            return "it looks music-related (Logic, GarageBand, plug-ins, samples, stems or audio)"
        try:
            siblings = {e.name for e in os.scandir(p.parent)}
        except OSError:
            return "its folder could not be checked"
        if siblings & PROJECT_MARKERS or any(
            n.endswith((".xcodeproj", ".xcworkspace")) for n in siblings
        ):
            return "its folder contains a code or project file"
        if origin == "planner":
            info = p.stat()
            stamps = [info.st_mtime] + (
                [info.st_birthtime] if self.use_birthtime and hasattr(info, "st_birthtime") else []
            )  # birth time catches a download that kept the server's old modified time
            if time.time() - max(stamps) < RECENT_SECONDS:
                return "it changed in the last 7 days"
            if ext in CODE_EXT:
                return "it is a source-code or project file"
            if (
                ext in INSTALLER_EXT
                and time.time() - max(stamps + [info.st_atime]) < INSTALLER_SECONDS
            ):
                return "it is an installer that was used in the last 30 days"
        return None

    def _propose_trash(self, source, pattern, paths, origin, turn):
        pid = uuid.uuid4().hex[:12]
        hits = []
        skipped = []
        if paths:
            if not isinstance(paths, (list, tuple)) or len(paths) > self.MAX_PLAN_FILES:
                raise FileProblem("bad_paths", f"Give up to {self.MAX_PLAN_FILES} file paths.")
            for raw in paths:
                p = self.resolve(raw)
                self.require_write(p)
                reason = self.protection(p, origin)
                if reason:
                    skipped.append({"name": p.name, "path": str(p), "reason": reason})
                else:
                    hits.append({"path": str(p)})
        else:
            src = self.resolve(source)
            self.require_write(src)
            found, scan = self._scan(
                src, pattern, False, limit=self.MAX_PLAN_FILES
            )  # loose files only: never recursive
            if scan["truncated"] or scan["issues"]:
                raise FileProblem(
                    "incomplete_selection",
                    "I could not check the complete selection. Narrow the search before changing files.",
                    **scan,
                )
            for hit in found:
                reason = self.protection(hit["path"], origin)
                if reason:
                    skipped.append({"name": hit["name"], "path": hit["path"], "reason": reason})
                else:
                    hits.append(hit)
        if not hits:
            if skipped:
                raise FileProblem(
                    "all_protected",
                    f"Nothing was moved to the trash: {len(skipped)} matching file(s) are protected, for example {skipped[0]['name']} ({skipped[0]['reason']}).",
                    skipped=skipped,
                )
            raise FileProblem("no_matches", "No matching files were found. Nothing will change.")
        ops = []
        total = 0
        for i, hit in enumerate(hits, 1):
            old = Path(hit["path"])
            target = self.trash_dir / pid / f"{i:03d}__{old.name}"
            fp = self._fingerprint(old)
            total += fp["size"]
            if total > self.MAX_PLAN_BYTES:
                raise FileProblem(
                    "too_large",
                    "This selection exceeds the 512 MB batch limit. Split it into smaller requests.",
                )
            ops.append(
                {"from": str(old), "to": str(target), "fingerprint": fp, "status": "planned"}
            )
        plan = {
            "id": pid,
            "session": self.session,
            "turn": turn,
            "created": time.time(),
            "action": "trash",
            "status": "pending",
            "ops": ops,
            "total_bytes": total,
            "count": len(ops),
            "origin": origin,
            "skipped": skipped,
        }
        with self._locked():
            state = self._load()
            state["plans"][pid] = plan
            self._save(state)
        names = summarize_ops([{"to": o["from"], "fingerprint": o["fingerprint"]} for o in ops])
        msg = f"I can move {len(ops)} file(s) to the trash: {names}. Nothing is deleted and you can say undo to put them back."
        if skipped:
            msg += (
                f" I left {len(skipped)} protected file(s) alone, for example {skipped[0]['name']}."
            )
        return result(
            True,
            "preview",
            msg + " Nothing has changed. Say yes to confirm or cancel to discard.",
            plan=plan,
            skipped=skipped,
        )

    def trash_size(self):
        total = 0
        if self.trash_dir.exists():
            for root, _dirs, files in os.walk(self.trash_dir):
                for f in files:
                    try:
                        total += os.lstat(os.path.join(root, f)).st_size
                    except OSError:
                        pass
        return total

    def trash_notice(self):
        """Spoken reminder once the trash is over 1 GB, else ''."""
        size = self.trash_size()
        return (
            f" The trash is now {human_size(size)}. Say empty the trash and I will show what is in it first."
            if size > TRASH_NOTICE_BYTES
            else ""
        )

    @response
    def trash_info(self):
        size = self.trash_size()
        n = sum(len(fs) for _r, _d, fs in os.walk(self.trash_dir)) if self.trash_dir.exists() else 0
        return result(
            True,
            "trash_info",
            f"The trash holds {n} file(s), {human_size(size)}."
            + (self.trash_notice() if size > TRASH_NOTICE_BYTES else ""),
            count=n,
            bytes=size,
        )

    @response
    def propose_empty_trash(self, turn=0):
        """Permanent deletion of everything in the trash: preview first, confirm in a separate turn. The only operation that cannot be undone."""
        files = []
        if self.trash_dir.exists():
            for root, _dirs, names in os.walk(self.trash_dir):
                for n in sorted(names):
                    f = Path(root) / n
                    try:
                        info = f.lstat()
                    except OSError:
                        continue
                    if stat.S_ISREG(info.st_mode):
                        files.append((f, info))
        if not files:
            raise FileProblem("trash_empty", "The trash is already empty.")
        pid = uuid.uuid4().hex[:12]
        ops = [
            {
                "from": str(f),
                "to": str(f),
                "fingerprint": {"size": i.st_size, "sha256": ""},
                "status": "planned",
            }
            for f, i in files
        ]
        total = sum(i.st_size for _f, i in files)
        plan = {
            "id": pid,
            "session": self.session,
            "turn": turn,
            "created": time.time(),
            "action": "purge",
            "status": "pending",
            "ops": ops,
            "total_bytes": total,
            "count": len(ops),
        }
        with self._locked():
            state = self._load()
            state["plans"][pid] = plan
            self._save(state)
        names = summarize_ops(
            [
                {"to": re.sub(r"^\d+__", "", Path(o["from"]).name), "fingerprint": o["fingerprint"]}
                for o in ops
            ]
        )
        return result(
            True,
            "preview",
            f"The trash holds {len(ops)} file(s), {human_size(total)}: {names}. Emptying it deletes them permanently and cannot be undone. Say yes to confirm or cancel to keep them.",
            plan=plan,
        )

    def _execute_purge(self, state, plan):
        gone = 0
        kept = 0
        for op in plan["ops"]:
            f = self.resolve(op["from"], internal=True)
            try:
                if (
                    f.exists()
                    and f.is_relative_to(self.trash_dir)
                    and f.lstat().st_size == op["fingerprint"]["size"]
                ):
                    f.unlink()
                    gone += 1
                    op["status"] = "purged"
                else:
                    kept += 1
                    op["status"] = "skipped"
            except OSError:
                kept += 1
                op["status"] = "skipped"
        # earlier trash moves can no longer be undone for the files that were purged
        purged = {op["from"] for op in plan["ops"] if op["status"] == "purged"}
        for other in state["plans"].values():
            if other["action"] == "trash":
                for op in other["ops"]:
                    if op["to"] in purged:
                        op["status"] = "purged"
                if all(op["status"] in ("purged", "undone") for op in other["ops"]) and other[
                    "status"
                ] in ("completed", "undo_partial"):
                    other["status"] = "purged"
        for d in sorted(
            (x for x in self.trash_dir.rglob("*") if x.is_dir()),
            key=lambda x: len(x.parts),
            reverse=True,
        ):
            try:
                d.rmdir()
            except OSError:
                pass
        plan["status"] = "completed"
        self._save(state)
        return result(
            True,
            "purged",
            f"Emptied the trash: {gone} file(s) deleted permanently."
            + (f" {kept} could not be removed." if kept else ""),
            plan=plan,
        )

    @response
    def history(self, limit=8, today=False):
        with self._locked():
            state = self._load()
        rows, midnight, older = [], time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)), None
        for pid in reversed(state["batches"]):
            plan = state["plans"][pid]
            if today and plan["created"] < midnight:
                older = older or time.strftime("%b %d", time.localtime(plan["created"]))
                continue
            when = time.strftime("%b %d %H:%M", time.localtime(plan["created"]))
            verb = {
                "trash": "trashed",
                "move": "moved",
                "copy": "copied",
                "rename": "renamed",
                "create": "created",
            }.get(plan["action"], plan["action"])
            first = Path(plan["ops"][0]["from"] or plan["ops"][0]["to"]).name
            if plan["action"] == "trash":
                first = re.sub(r"^\d+__", "", Path(plan["ops"][0]["to"]).name)
            rows.append(
                {
                    "id": pid,
                    "when": when,
                    "action": plan["action"],
                    "count": len(plan["ops"]),
                    "status": plan["status"],
                    "first": first,
                    "text": f"{when}: {verb} {len(plan['ops'])} file(s), {first}{' and others' if len(plan['ops']) > 1 else ''} ({plan['status']})",
                }
            )
            if len(rows) >= max(1, min(int(limit), 30)):
                break
        if not rows and today:
            return result(
                True,
                "history",
                "I have not changed any files today."
                + (f" The last change was on {older}." if older else ""),
                items=[],
            )
        if not rows:
            return result(True, "history", "I have not changed any files yet.", items=[])
        spoken = "; ".join(r["text"] for r in rows[:3])
        return result(
            True,
            "history",
            f"Recent file changes: {spoken}."
            + (" The rest is on screen." if len(rows) > 3 else ""),
            items=rows,
        )

    @response
    def show(self, plan_id):
        with self._locked():
            plan = self._load()["plans"].get(plan_id)
        if not plan:
            raise FileProblem("unknown_plan", "I could not find that file plan.")
        return result(
            True,
            "preview",
            f"The plan is to {plan['action']} {len(plan['ops'])} file(s). Its status is {plan['status']}.",
            plan=plan,
        )

    def _preflight(self, plan):
        if plan["session"] != self.session:
            raise FileProblem(
                "stale_session",
                "This preview is from a previous session. Make a new plan before changing files.",
            )
        if time.time() - plan["created"] > self.PLAN_TTL:
            raise FileProblem("expired", "That preview has expired. Please make a new plan.")
        if plan["action"] == "purge":
            return
        for op in plan["ops"]:
            if plan["action"] != "copy" and op["from"]:
                self.require_write(op["from"])
            if plan["action"] != "purge":
                self.require_write(op["to"]) if plan["action"] != "trash" else None
            if op["from"] and not self._matches(op["from"], op["fingerprint"], identity=True):
                raise FileProblem(
                    "changed",
                    "A selected file changed or disappeared. Review a new plan before continuing.",
                    path=op["from"],
                )
            d = self.resolve(op["to"], new=True, internal=True)
            if d.exists():
                raise FileProblem("collision", f"{d.name} now exists. Nothing will be overwritten.")

    @staticmethod
    def _sync_directory(path):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _publish(self, op, action, journal=None):
        dst = self.resolve(op["to"], new=True, internal=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        self.resolve(dst.parent, internal=True)
        src = self.resolve(op["from"], internal=True) if op["from"] else None
        if src and action in MOVELIKE and src.stat().st_dev == dst.parent.stat().st_dev:
            # Exclusive publication: unlike rename/shutil.move, a new target cannot be overwritten.
            op["publication_fingerprint"] = op["fingerprint"]
            if journal:
                journal()
            os.link(src, dst, follow_symlinks=False)
            if not self._matches(src, op["fingerprint"], identity=True) or not self._matches(
                dst, op["fingerprint"]
            ):
                owned = dst.lstat()
                if (owned.st_dev, owned.st_ino) == (
                    op["fingerprint"]["device"],
                    op["fingerprint"]["inode"],
                ):
                    dst.unlink()
                raise FileProblem(
                    "changed",
                    "The source or destination changed while moving. The original was retained; any unexpected destination needs review.",
                )
            src.unlink()
        else:
            fd, temp = tempfile.mkstemp(prefix=".julius-stage-", dir=dst.parent)
            os.close(fd)
            try:
                if src:
                    shutil.copy2(src, temp)
                else:
                    Path(temp).write_text(op["content"], encoding="utf-8")
                with open(temp, "rb") as f:
                    os.fsync(f.fileno())
                h = hashlib.sha256()
                with open(temp, "rb") as f:
                    for block in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(block)
                if h.hexdigest() != op["fingerprint"]["sha256"]:
                    raise FileProblem(
                        "changed", "The source changed while copying. No destination was published."
                    )
                if src and not self._matches(src, op["fingerprint"], identity=True):
                    raise FileProblem(
                        "changed", "The source changed. No destination was published."
                    )
                staged = Path(temp).stat()
                op["publication_fingerprint"] = {
                    "size": staged.st_size,
                    "mtime_ns": staged.st_mtime_ns,
                    "device": staged.st_dev,
                    "inode": staged.st_ino,
                    "sha256": h.hexdigest(),
                }
                if journal:
                    journal()
                os.link(temp, dst, follow_symlinks=False)
                if src and action in MOVELIKE:
                    if not self._matches(src, op["fingerprint"], identity=True):
                        raise FileProblem(
                            "changed",
                            "The original changed and was retained. The copied destination needs review.",
                        )
                    src.unlink()
            finally:
                Path(temp).unlink(missing_ok=True)
        self._sync_directory(dst.parent)
        if src and action in MOVELIKE and src.parent != dst.parent:
            self._sync_directory(src.parent)
        return self._fingerprint(dst)

    @response
    def execute(self, plan_id, *, confirmed=False, turn=0):
        if confirmed is not True:
            raise FileProblem(
                "confirmation_required", "Please confirm the displayed file plan before running it."
            )
        with self._locked():
            state = self._load()
            plan = state["plans"].get(plan_id)
            if not plan:
                raise FileProblem("unknown_plan", "I could not find that file plan.")
            if plan["status"] != "pending":
                raise FileProblem(
                    "already_processed",
                    "That plan has already been processed. Review its result or create a new plan.",
                )
            if turn <= plan["turn"]:
                raise FileProblem(
                    "confirmation_required",
                    "Preview the plan first, then confirm it in a separate turn.",
                )
            self._preflight(plan)
            if plan["action"] == "purge":
                return self._execute_purge(state, plan)
            plan["status"] = "running"
            state["batches"].append(plan_id)
            self._save(state)
            for op in plan["ops"]:
                op["status"] = "applying"
                self._save(state)
                try:
                    op["result_fingerprint"] = self._publish(
                        op, plan["action"], journal=lambda: self._save(state)
                    )
                    op["status"] = "completed"
                    op.pop("content", None)
                except (OSError, FileProblem) as exc:
                    op["error"] = str(exc)
                    op["status"] = "needs_review"
                    plan["status"] = "partial"
                    self._save(state)
                    return result(
                        False,
                        "partial",
                        f"Stopped after {sum(o['status'] == 'completed' for o in plan['ops'])} successful changes. The remaining files need review.",
                        plan=plan,
                    )
                self._save(state)
            plan["status"] = "completed"
            self._save(state)
        verbs = {
            "move": "Moved",
            "copy": "Copied",
            "rename": "Renamed",
            "create": "Created",
            "trash": "Moved to the trash:",
        }
        extra = self.trash_notice() if plan["action"] == "trash" else ""
        return result(
            True,
            "completed",
            f"{verbs[plan['action']]} {len(plan['ops'])} file(s). Say undo to reverse the change."
            + extra,
            plan=plan,
        )

    @response
    def cancel(self, plan_id):
        with self._locked():
            state = self._load()
            plan = state["plans"].get(plan_id)
            if not plan or plan["status"] != "pending":
                raise FileProblem("not_pending", "There is no pending plan to cancel.")
            plan["status"] = "cancelled"
            for op in plan["ops"]:
                op.pop("content", None)
            self._save(state)
        return result(True, "cancelled", "Cancelled. No files were changed.")

    @response
    def undo(self):
        with self._locked():
            state = self._load()
            candidates = [
                state["plans"][pid]
                for pid in reversed(state["batches"])
                if state["plans"][pid]["status"] not in ("undone", "purged")
            ]
            if not candidates:
                return result(True, "nothing_to_undo", "There are no file changes to undo.")
            plan = candidates[0]
            reversed_count = 0
            issues = []
            for op in reversed(plan["ops"]):
                if op["status"] == "undone":
                    continue
                if op["status"] == "purged":
                    issues.append(
                        {
                            "path": op["from"],
                            "reason": "It was deleted permanently when the trash was emptied.",
                        }
                    )
                    continue
                dst = self.resolve(op["to"], new=True, internal=True)
                if op["status"] == "undoing":
                    if (
                        plan["action"] in MOVELIKE
                        and not dst.exists()
                        and self._matches(
                            op["from"], op.get("result_fingerprint", op["fingerprint"])
                        )
                    ):
                        op["status"] = "undone"
                        reversed_count += 1
                        self._save(state)
                        continue
                    if plan["action"] in {"copy", "create"} and not dst.exists():
                        op["status"] = "undone"
                        reversed_count += 1
                        self._save(state)
                        continue
                # Recover a crash between publication and saving its final status.
                if op["status"] in {"applying", "needs_review"}:
                    src_exists = bool(op["from"] and Path(op["from"]).exists())
                    if not dst.exists():
                        op["status"] = "undone"
                        self._save(state)
                        continue
                    publication = op.get(
                        "publication_fingerprint",
                        op["fingerprint"] if plan["action"] in MOVELIKE else {},
                    )
                    if not publication or not self._matches(dst, publication, identity=True):
                        issues.append(
                            {
                                "path": op["to"],
                                "reason": "Interrupted operation cannot be resolved automatically.",
                            }
                        )
                        continue
                    if src_exists and plan["action"] in MOVELIKE:
                        src = Path(op["from"])
                        if os.path.samefile(src, dst) and self._matches(
                            src, op["fingerprint"], identity=True
                        ):
                            dst.unlink()
                            op["status"] = "undone"
                            reversed_count += 1
                            self._save(state)
                            continue
                        issues.append(
                            {
                                "path": op["to"],
                                "reason": "Both paths exist after interruption; review is required.",
                            }
                        )
                        continue
                if op["status"] == "planned":
                    op["status"] = "undone"
                    self._save(state)
                    continue
                expected = op.get(
                    "result_fingerprint", op.get("publication_fingerprint", op["fingerprint"])
                )
                if not dst.exists() or not self._matches(dst, expected, identity=True):
                    issues.append(
                        {
                            "path": op["to"],
                            "reason": "The result was changed or removed; it was left untouched.",
                        }
                    )
                    continue
                if plan["action"] in MOVELIKE:
                    original = self.resolve(op["from"], new=True, internal=True)
                    try:
                        self.require_write(original)
                    except FileProblem as exc:
                        issues.append({"path": str(original), "reason": str(exc)})
                        continue
                    if original.exists():
                        issues.append(
                            {
                                "path": str(original),
                                "reason": "The original location is occupied; nothing was overwritten.",
                            }
                        )
                        continue
                    reverse = {
                        "from": str(dst),
                        "to": str(original),
                        "fingerprint": self._fingerprint(dst),
                    }
                    op["status"] = "undoing"
                    self._save(state)
                    try:
                        self._publish(reverse, "move")
                    except (OSError, FileProblem) as exc:
                        issues.append({"path": str(dst), "reason": str(exc)})
                        continue
                else:
                    # Only remove the exact, unchanged output Julius created/copied.
                    op["status"] = "undoing"
                    self._save(state)
                    dst.unlink()
                    self._sync_directory(dst.parent)
                op["status"] = "undone"
                reversed_count += 1
                self._save(state)
            plan["status"] = "undo_partial" if issues else "undone"
            self._save(state)
        return result(
            not issues,
            "undo_partial" if issues else "undone",
            f"Reversed {reversed_count} file change(s)."
            + (" Some files were left untouched and need review." if issues else ""),
            plan=plan,
            issues=issues,
        )
