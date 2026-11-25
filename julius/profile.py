"""Default launch profile: cloud brain (Haiku/Sonnet tiers from julius.toml) with the MLX voice.
run_julius.sh's `cloud` profile sets the same values; test_julius_default_profile.py keeps them in sync."""

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from julius import identity

CLOUD_DEFAULTS = {
    "JULIUS_BACKEND": "cloud",
    "JULIUS_MODEL": "claude-haiku-5-5",
    "JULIUS_FALLBACK_MODEL": "llama3.2:3b",
    "JULIUS_VISION": "0",
    "JULIUS_TTS": "qwen-mlx",
    "JULIUS_KEEP_VOICE": "1",
    "JULIUS_MLX_KEEP_WORKER": "1",
    "JULIUS_MLX_ASR_KEEP_VOICE": "1",
    "JULIUS_ASR_RESIDENT": "1",
    "JULIUS_SILENCE_SECONDS": "0.6",
}


def apply_cloud_defaults(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Fill in any cloud-profile setting the caller has not already set; explicit values always win."""
    environ = os.environ if environ is None else environ
    for key, value in CLOUD_DEFAULTS.items():
        environ.setdefault(key, value)
    return environ


# ---- who the assistant is talking to ([profile] in julius.toml / julius.local.toml; neutral defaults) ----
def _profile() -> dict[str, str | list[str]]:
    import julius.config as cfg

    return cfg.load_config().get("profile", {})


def user_name() -> Any:
    """The configured name, or "the user" when none is set."""
    return (_profile().get("name") or "").strip() or "the user"


def home_city() -> str:
    """The default city for weather and forecasts, or "" when none is set."""
    return (_profile().get("home_city") or "").strip()


def apply(text: str, profile: None = None) -> str:
    """Swap the placeholder user ("the user") and placeholder city in a prompt for this install's profile. Applied to the system prompt and tool descriptions on their way to the model. With no name set the text is left as written."""
    p = profile if profile is not None else _profile()
    name = (p.get("name") or "").strip()
    city = (p.get("home_city") or "").strip() or "the user's home city"
    if name:
        text = re.sub(
            r"\b([Tt])he user(?:'s)?\b",
            lambda m: name + ("'s" if m.group(0).endswith("'s") else ""),
            text,
        )
    return text.replace(PLACEHOLDER_CITY, city)


PLACEHOLDER_CITY = "Springfield, Illinois"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def local_config_path() -> Path:
    import julius.config as cfg

    return Path(os.environ.get("JULIUS_LOCAL_CONFIG") or cfg.ROOT / "julius.local.toml")


def needs_setup() -> bool:
    """True until a name or a home city has been configured."""
    p = _profile()
    return not ((p.get("name") or "").strip() or (p.get("home_city") or "").strip())


def setup(ask: Callable[[str], str] = input, say: Callable[[str], Any] = print) -> Path | None:
    """First-run questions: name, home city, words Whisper should know. Writes [profile] to julius.local.toml (gitignored). Never overwrites an existing [profile]."""
    path = local_config_path()
    if path.is_file() and "[profile]" in path.read_text():
        say(f"{path} already has a [profile] section; edit it there.")
        return None
    name = ask(f"What should {identity.name()} call you? (blank to skip) ").strip()
    city = ask("Home city for weather, e.g. Reno, Nevada (blank to skip) ").strip()
    words = ask("Names Whisper should recognise, comma separated (blank to skip) ")
    vocabulary = [w.strip() for w in words.split(",") if w.strip()]
    block = (
        "[profile]\n"
        f"name = {_toml_string(name)}\n"
        f"home_city = {_toml_string(city)}\n"
        f"vocabulary = [{', '.join(_toml_string(w) for w in vocabulary)}]\n"
    )
    existing = path.read_text() if path.is_file() else ""
    path.write_text(existing + ("\n" if existing and not existing.endswith("\n\n") else "") + block)
    say(f"Saved to {path} (gitignored). Edit it any time.")
    return path


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["setup"]:
        setup()
    else:
        print("usage: python -m julius.profile setup")
