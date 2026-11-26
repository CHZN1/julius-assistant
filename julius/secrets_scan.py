"""Find secrets in file contents before they leave a sandbox (threat model M2): the exact values of the keys this install
holds, and gitleaks-style patterns for the common token formats. Findings name the file and the kind of secret, never the
value, so a refusal can be spoken and logged without leaking what it found."""

from __future__ import annotations

import os
import re
from pathlib import Path

# environment variables whose values are secrets on this Mac (their exact values are searched for)
SECRET_ENV = (
    "ANTHROPIC_API_KEY",
    "ODDS_API_KEY",
    "SERPAPI_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "HF_TOKEN",
)
PATTERNS = (  # (kind, regex): the formats gitleaks' default rules catch most often
    ("an Anthropic API key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("an OpenAI API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{32,}")),
    ("an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "a GitHub token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    ),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("a Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("a Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    (
        "a private key",
        re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"),
    ),
    (
        "a hard-coded secret",
        re.compile(
            r"""(?i)\b(?:api[_-]?key|secret|token|passw(?:or)?d|auth)\w*\s*[:=]\s*["']([A-Za-z0-9_\-+/=]{24,})["']"""
        ),
    ),
)
MAX_BYTES = 2_000_000  # larger files are scanned up to here
TEST_VALUES = ("sk-test", "sk-ant-TEST", "NOT-A-REAL", "example", "xxxx")


def known_values(extra: tuple[str, ...] = ()) -> list[str]:
    """The secret values this process knows about (environment and any passed in), long enough to be unambiguous."""
    vals = [os.environ.get(k, "") for k in SECRET_ENV] + list(extra)
    return [v for v in {v.strip() for v in vals if v} if len(v) >= 12]


def scan_text(text: str, values: list[str] | None = None) -> list[str]:
    """Kinds of secret found in `text` (empty when clean)."""
    found = []
    for v in values if values is not None else known_values():
        if v and v in text:
            found.append("one of this Mac's own keys")
            break
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            hit = m.group(0)
            if not any(t.lower() in hit.lower() for t in TEST_VALUES):
                found.append(kind)
                break
    return found


def scan_file(path: Path, values: list[str] | None = None) -> list[str]:
    try:
        data = Path(path).read_bytes()[:MAX_BYTES]
    except OSError:
        return []
    if b"\x00" in data[:4096]:  # binary: check only for the exact key values
        vals = values if values is not None else known_values()
        return ["one of this Mac's own keys"] if any(v.encode() in data for v in vals) else []
    return scan_text(data.decode("utf-8", "replace"), values)
