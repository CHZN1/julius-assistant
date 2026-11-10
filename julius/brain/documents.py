"""Scoped text extraction and literal search with citations; parser is isolated."""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from julius.agents.file_agent import FileAgent, FileProblem
from julius.paths import REPO_ROOT

MAX_BYTES = 20 * 1024 * 1024


def document_python() -> str:
    configured = os.environ.get("JULIUS_DOCUMENT_PYTHON")
    if configured:
        return configured
    bundled = (
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    )
    return str(bundled) if bundled.exists() else sys.executable


def _read_bounded(resolved: Path) -> bytes:
    """Read a regular file of at most MAX_BYTES, failing if it changes while it is read."""
    fd = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Please select a regular document file.")
        if before.st_size > MAX_BYTES:
            raise ValueError("Document exceeds the 20 MiB limit.")
        raw = source.read(MAX_BYTES + 1)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Document changed while it was read. Please try again.")
    if len(raw) > MAX_BYTES:
        raise ValueError("Document exceeds the 20 MiB limit.")
    return raw


def _search_chunks(chunks: list[dict[str, str]], query: str) -> list[dict[str, str]]:
    """Up to 20 passages containing the phrase, each with some surrounding text."""
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
        raise ValueError("Use a search phrase of 1–200 characters.")
    phrase = query.casefold()
    matches = []
    for chunk in chunks:
        pos = chunk["text"].casefold().find(phrase)
        if pos >= 0:
            matches.append(
                {
                    "location": chunk["location"],
                    "text": chunk["text"][max(0, pos - 120) : pos + len(query) + 240],
                }
            )
        if len(matches) == 20:
            break
    return matches


def read_document(
    path: str, query: str | None = None, file_agent: FileAgent | None = None
) -> dict[str, Any]:
    """Extract (or search) a PDF/DOCX in a resource-limited worker; always returns a result dict."""
    if file_agent is None:
        import julius.agents.files as jf

        file_agent = jf.agent()
    try:
        resolved = file_agent.resolve(path)
        raw = _read_bounded(resolved)
        process = subprocess.run(
            [document_python(), "-m", "julius.brain.document_worker", resolved.suffix.lower()],
            cwd=str(REPO_ROOT),
            input=raw,
            capture_output=True,
            timeout=12,
        )
        if process.returncode != 0:
            raise ValueError("The document parser stopped at its resource limit or failed.")
        out = json.loads(process.stdout)
        out["agent"] = "documents"
        data = out["data"]
        data["path"] = str(resolved)
        chunks = data.get("chunks", [])
        if query is not None:
            chunks = data["matches"] = _search_chunks(chunks, query)
            if out["ok"]:
                out["message"] = (
                    "Found matching passages." if chunks else "No matches in the extracted portion."
                )
        if out["ok"]:
            lines = [f"{resolved.name}: {out['message']}"]
            lines += [f"[{c['location']}] {c['text']}" for c in chunks]
            if data.get("truncated"):
                lines.append("Partial extraction: only the bounded portion was searched/read.")
            lines += data.get("warnings", [])
            out["message"] = "\n".join(lines)
        return out
    except FileProblem as exc:
        return dict(ok=False, code=exc.code, message=str(exc), data={})
    except subprocess.TimeoutExpired:
        return dict(
            ok=False,
            code="timeout",
            message="Document extraction exceeded 12 seconds; the parser was stopped.",
            data={},
        )
    except Exception as exc:
        return dict(
            ok=False,
            code="extraction_failed",
            message=f"Could not read that document: {exc}",
            data={},
        )
