"""Runtime configuration: julius.toml defaults, environment overrides, and the .env API key.

The key is read from the environment or the project's .env file and is never printed or logged;
use describe_key() for a safe status line.
"""

import datetime
import os
import tomllib
from pathlib import Path
from typing import Any

from julius.paths import REPO_ROOT

ROOT = REPO_ROOT
DEFAULTS = {
    "brain": {"mode": "cloud"},
    "models": {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"},
    "spend": {"daily_cap_usd": 5.0, "offline_at_multiple": 2.0},
    "orchestrator": {"max_steps": 8, "subagent_max_steps": 6},
    "research": {
        "max_steps": 8,
        "max_pages": 5,
        "max_usd": 0.10,
        "max_bytes": 500000,
        "max_page_chars": 6000,
        "save": True,
        "keep": 50,
    },
    "offline": {"model": "llama3.2:3b", "retry_seconds": 60},
    "voice": {"keep_loaded": True},
    "memory": {"announce_auto": True},
    "service": {
        "wake_hotkey": "<ctrl>+<alt>+j",
        "log_transcripts": False,
        "log_transcripts_until": "",
    },
    "code": {
        "model": "claude-opus-5-5",
        "default_cap_usd": 1.5,
        "max_cap_usd": 5.0,
        "timeout_minutes": 20,
        "daily_cap_usd": 5.0,
        "projects_root": "~/dev",
        "binary": "claude",
    },
    "files": {"folders": {"Documents": "write", "Desktop": "write", "Downloads": "write"}},
    "barge_in": {"hotkey": "<ctrl>+<alt>+s", "voice": True},
    "events": {
        "enabled": True,
        "port": 8765,
    },  # read-only WebSocket on 127.0.0.1 for a UI (julius_events.py)
    "ui": {
        "enabled": True,
        "port": 8775,
        "typed_reply": "both",
    },  # write channel for the orb app (julius_uichannel.py): typed text and dropped files. typed_reply: both = speak and show, text = show only
    "audio": {"duplex": True, "duplex_rate": 48000, "fallback_seconds": 3.0, "raw_backfill": False},
    "wake": {
        "engine": "whisper",
        "threshold_low": 0.05,
        "model": "",
        "learn": True,
        "learn_after": 20,
        "starter_threshold": 0.0005,
        "level_floor": 0.0015,
        "speaker_threshold": 0.25,
        "verifier": "~/.julius/wake_verifier.pkl",
    },
    "asr": {
        "vocabulary_hint": True,
        "vocabulary": ["Julius"],
        "nhl_teams": [],
        "nfl_teams": [],
    },  # your own names go in [profile] vocabulary (julius.local.toml)
    "profile": {
        "name": "",
        "home_city": "",
        "vocabulary": [],
    },  # neutral defaults; put your values in julius.local.toml (gitignored)
}


def load_dotenv(path: Path | None = None) -> bool:
    """Load KEY=VALUE lines from .env into os.environ without overriding existing variables."""
    path = Path(path or os.environ.get("JULIUS_ENV_FILE") or ROOT / ".env")
    if not path.is_file():
        return False
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value
    return True


def transcripts_logging(
    config: dict[str, dict[str, str | bool] | dict[str, bool]] | None = None,
    today: datetime.date | None = None,
) -> bool:
    """Whether what he heard and said may go into the JSON log right now: [service] log_transcripts is true and, when log_transcripts_until (YYYY-MM-DD) is set,
    today is not past it. Diagnostics windows switch themselves off."""
    service = (config if config is not None else load_config()).get("service") or {}
    if not service.get("log_transcripts"):
        return False
    until = str(service.get("log_transcripts_until") or "").strip()
    if not until:
        return True
    try:
        return (today or datetime.date.today()) <= datetime.date.fromisoformat(until)
    except ValueError:
        return False  # an unreadable date counts as expired: privacy wins


_transcripts_cache = [0.0, False]


def transcripts_logging_cached(seconds=60.0, clock=None):
    """transcripts_logging() re-read at most every `seconds`, for the log writer (so a long-running service notices the end of the window and edits to julius.toml)."""
    import time

    now = (clock or time.monotonic)()
    if now - _transcripts_cache[0] >= seconds:
        try:
            _transcripts_cache[1] = transcripts_logging()
        except Exception:
            _transcripts_cache[1] = False
        _transcripts_cache[0] = now
    return _transcripts_cache[1]


def load_config(path: Path | None = None) -> dict[str, Any]:
    """julius.toml merged over DEFAULTS section by section. JULIUS_BRAIN overrides brain.mode."""
    config = {section: dict(values) for section, values in DEFAULTS.items()}
    path = Path(path or os.environ.get("JULIUS_CONFIG") or ROOT / "julius.toml")
    if path.is_file():
        with path.open("rb") as handle:
            for section, values in tomllib.load(handle).items():
                config.setdefault(section, {}).update(values)
    local = Path(
        os.environ.get("JULIUS_LOCAL_CONFIG") or path.parent / "julius.local.toml"
    )  # your own values, gitignored; merged over julius.toml
    if local.is_file():
        with local.open("rb") as handle:
            for section, values in tomllib.load(handle).items():
                config.setdefault(section, {}).update(values)
    if os.environ.get("JULIUS_BRAIN"):
        config["brain"]["mode"] = os.environ["JULIUS_BRAIN"]
    if config["brain"]["mode"] not in ("cloud", "local"):
        raise ValueError("brain.mode must be 'cloud' or 'local'")
    if not config["spend"]["daily_cap_usd"] > 0:
        raise ValueError("spend.daily_cap_usd must be positive")
    if not config["spend"]["offline_at_multiple"] > 1:
        raise ValueError("spend.offline_at_multiple must be greater than 1")
    if config["ui"]["typed_reply"] not in ("both", "text"):
        raise ValueError("ui.typed_reply must be 'both' or 'text'")
    code = config["code"]
    if not (0 < code["default_cap_usd"] <= code["max_cap_usd"]):
        raise ValueError("code.default_cap_usd must be positive and not above code.max_cap_usd")
    if not (code["timeout_minutes"] > 0 and code["daily_cap_usd"] > 0):
        raise ValueError("code.timeout_minutes and code.daily_cap_usd must be positive")
    return config


def api_key() -> str | None:
    load_dotenv()
    return os.environ.get("ANTHROPIC_API_KEY") or None


def describe_key() -> str:
    """Safe status for logs: whether a key is present and how long it is, never the value."""
    key = api_key()
    return f"set ({len(key)} chars)" if key else "missing"


WAKE_CONTRAST = "Julius. Hey Julius. Julia. Julian. Julie. Jules."


def asr_initial_prompt(
    config: dict[str, Any] | None = None,
    wake: bool = False,
) -> str:
    """The Whisper initial_prompt built from [asr] in julius.toml, or None when the hint is disabled.

    wake=False (default): the vocabulary only, for transcribing a request while Julius is awake.
    wake=True: for the utterance that may contain the wake word. It opens with the wake word next to its sound-alikes and leaves
    the bare name out of the vocabulary list, because a vocabulary hint that lists "Julius" alone makes Whisper hear Julia and
    Julian as Julius (a false wake). Only the wake check uses it (evals/wake_hint_study.py)."""
    asr = (config or load_config())["asr"]
    if not asr.get("vocabulary_hint", True):
        return None
    words = (
        list(asr.get("vocabulary", []))
        + list((config or load_config()).get("profile", {}).get("vocabulary", []))
        + list(asr.get("nhl_teams", []))
        + list(asr.get("nfl_teams", []))
    )
    if not wake:
        return ", ".join(dict.fromkeys(words)) + "." if words else None
    words = [w for w in words if w.strip().lower() != "julius"]
    return WAKE_CONTRAST + " " + ", ".join(dict.fromkeys(words)) + "." if words else None
