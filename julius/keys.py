"""API keys: from what you exported yourself, then the macOS Keychain, then the owner-only .env.

Keychain items are generic passwords with service "julius:NAME" (store one with `scripts/jkey set NAME`; the value is typed at
a hidden prompt, never on a command line). The older item "julius-anthropic-key" is NOT read: on 2026-11-26 it held a
different key from .env (probably an old one), and silently switching to it could break the service.
A variable that came from .env does not hide the Keychain: .env is the fallback. Keys are never printed or logged.
JULIUS_NO_KEYCHAIN=1 skips the Keychain (the tests set it, so they never read your real one)."""

from __future__ import annotations

import os
import subprocess

_cache: dict[str, str] = {}


def keychain(name: str) -> str | None:
    if os.environ.get("JULIUS_NO_KEYCHAIN") == "1":
        return None
    if name in _cache:
        return _cache[name]
    user = os.environ.get("USER") or ""
    for service in (f"julius:{name}",):
        try:
            r = subprocess.run(
                ["/usr/bin/security", "find-generic-password", "-a", user, "-s", service, "-w"],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        value = r.stdout.strip() if r.returncode == 0 else ""
        if value:
            _cache[name] = value
            return value
    return None


def get(name: str) -> str | None:
    """The key's value, or None."""
    from julius import config

    config.load_dotenv()
    value = os.environ.get(name, "").strip()
    if value and name not in config.DOTENV_LOADED:  # exported by you for this run: it wins
        return value
    return keychain(name) or value or None


def source(name: str) -> str:
    """Where the key comes from ("environment", "keychain", ".env" or "missing"), for status lines; never the value."""
    from julius import config

    config.load_dotenv()
    if os.environ.get(name) and name not in config.DOTENV_LOADED:
        return "environment"
    if keychain(name):
        return "keychain"
    return ".env" if os.environ.get(name) else "missing"


def reset() -> None:
    _cache.clear()
