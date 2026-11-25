"""Scripted test turns (JULIUS_VALIDATION_SCRIPT) never touch the real memory, conversation list or cost ledger.

Each scripted run gets a fresh folder for the three paths below, unless the caller set them itself (the evals do). Called from
`python -m julius` before anything reads them, and again from boot (a no-op the second time). JULIUS_TEST_KEEP_MEMORY=1 opts out,
for a test that must read the real memory. Light on purpose: no julius imports."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

PATHS = {
    "JULIUS_MEMORY_DIR": "memory",  # facts, sessions, file-agent state (and costs.jsonl by default)
    "JULIUS_STATE_DIR": "memory",  # the file tools' state (julius/agents/files.py reads it at import)
    "JULIUS_CONVERSATIONS_DB": "conversations.sqlite3",  # the conversation list (~/.julius by default)
    "JULIUS_COSTS_FILE": "memory/costs.jsonl",  # the cost ledger
}
MARK = "JULIUS_TEST_RUN_DIR"


def isolate(env: dict[str, str] | None = None) -> Path | None:
    """-> the fresh folder when this call made one; None when not a scripted run, opted out, or already isolated."""
    env = os.environ if env is None else env
    if not env.get("JULIUS_VALIDATION_SCRIPT") or env.get("JULIUS_TEST_KEEP_MEMORY") == "1":
        return None
    if env.get(MARK):
        return None
    root = Path(tempfile.mkdtemp(prefix="julius-test-run-"))
    (root / "memory").mkdir()
    for key, rel in PATHS.items():
        env.setdefault(key, str(root / rel))
    env[MARK] = str(root)
    return root
