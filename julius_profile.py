"""Default launch profile: cloud brain (Haiku/Sonnet tiers from julius.toml) with the MLX voice.
run_julius.sh's `cloud` profile sets the same values; test_julius_default_profile.py keeps them in sync."""
import os

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

def apply_cloud_defaults(environ=None):
    """Fill in any cloud-profile setting the caller has not already set; explicit values always win."""
    environ = os.environ if environ is None else environ
    for key, value in CLOUD_DEFAULTS.items():
        environ.setdefault(key, value)
    return environ
