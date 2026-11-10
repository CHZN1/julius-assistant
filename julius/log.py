"""Structured JSON logs with rotation for the always-on service.

Everything Julius prints (stdout and stderr, including tracebacks) is also written as one JSON object per line to
~/Library/Logs/Julius/julius.jsonl, rotated at 5 MB with 5 old files kept. What you say and what he answers is redacted unless
[service] log_transcripts = true. API keys are always redacted."""

import contextlib
import io
import json
import logging
import os
import re
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

LOG_DIR = Path(os.environ.get("JULIUS_LOG_DIR", "~/Library/Logs/Julius")).expanduser()
_KEY = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|[A-Fa-f0-9]{32,})")
_SPEECH = re.compile(r"^\s*(YOU\b|JULIUS:|\[heard\b|\[ignored: Julius heard)")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_NOISE = re.compile(r"%\||Download complete|Reconstruction complete|^\s*$")


def make_logger(path: Any | None = None, max_bytes: int = 5_000_000, backups: int = 5) -> Any:
    path = Path(path or LOG_DIR / "julius.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("julius.json")
    log.setLevel(logging.INFO)
    log.propagate = False
    for h in list(log.handlers):
        log.removeHandler(h)
    h = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(h)
    return log


def record(level: Any, event: Any, msg: str = "", log_transcripts: bool = False, **fields) -> Any:
    if not log_transcripts and _SPEECH.match(msg):
        msg = "[transcript redacted]"
    return json.dumps(
        {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "level": level,
            "event": event,
            "msg": _KEY.sub("[redacted]", msg),
            **fields,
        },
        ensure_ascii=False,
    )


class Tee(io.TextIOBase):
    """File-like wrapper: passes text through and logs every complete line."""

    def __init__(self, inner: Any, log: Any, level: Any, log_transcripts: bool = False) -> None:
        self.inner, self.log, self.level, self.log_transcripts, self.buf = (
            inner,
            log,
            level,
            log_transcripts,
            "",
        )

    def write(self, s: str) -> Any:
        with contextlib.suppress(Exception):
            self.inner.write(s)
        self.buf += _ANSI.sub("", s).replace("\r", "\n")
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip() and not _NOISE.search(line):
                self.log.info(
                    record(
                        self.level,
                        "console",
                        line.strip(),
                        self.log_transcripts()
                        if callable(self.log_transcripts)
                        else self.log_transcripts,
                    )
                )
        return len(s)

    def flush(self) -> None:
        with contextlib.suppress(Exception):
            self.inner.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


_logger = None


def install(log_transcripts: bool = False, path: Any | None = None) -> Any:
    """Tee stdout/stderr into the JSON log. `log_transcripts` is a bool or a callable asked for every line (so a time-limited diagnostics window ends by itself). Returns the logger."""
    global _logger
    _logger = make_logger(path)
    sys.stdout = Tee(sys.stdout, _logger, "info", log_transcripts)
    sys.stderr = Tee(sys.stderr, _logger, "error", log_transcripts)
    event("start", pid=os.getpid())
    return _logger


def console(*args: Any, **kwargs: Any) -> None:
    """Write a line to the console (stdout). Library code uses this instead of print: the Tee installed by
    `install` records everything written here as a `console` event, with the same redaction rules."""
    print(*args, **kwargs)


def event(name: str, msg: str = "", **fields) -> None:
    """A structured event (start, quiet, wake, stop, crash ...); a no-op when the JSON log is not installed."""
    if _logger is not None:
        _logger.info(record("info", name, msg, True, **fields))
