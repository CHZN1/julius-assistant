"""Default launch profile: cloud brain (Haiku/Sonnet tiers from julius.toml) with the MLX voice.
run_julius.sh's `cloud` profile sets the same values; test_julius_default_profile.py keeps them in sync."""

import os
import re
from typing import Any

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
    """Swap the author's placeholder name and city in a prompt for this install's profile. Applied to the system prompt and tool descriptions on their way to the model."""
    p = profile if profile is not None else _profile()
    name = (p.get("name") or "").strip() or "the user"
    city = (p.get("home_city") or "").strip() or "the user's home city"
    text = re.sub(
        r"\bRoy(?:'s)?\b", lambda m: name + ("'s" if m.group(0).endswith("'s") else ""), text
    )
    return text.replace("Springfield, Illinois", city)
