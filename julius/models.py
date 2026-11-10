"""First-run model downloads: `python -m julius.models fetch`.

Fetches everything Julius needs from the network once, so the first start does not wait on downloads:
the wake-word feature models (openWakeWord), the speech recognition model (MLX Whisper) and the voice
model (Qwen3-TTS, into the cache the voice worker reads). Safe to run again; cached files are reused."""

import os
import sys
from collections.abc import Callable
from pathlib import Path

from julius.paths import REPO_ROOT

WHISPER_REPO = "mlx-community/whisper-small.en-mlx"
VOICE_REPO = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"


def voice_cache_dir() -> Path:
    """The Hugging Face cache the voice worker uses (julius.audio.mlx_tts_worker)."""
    return Path(os.environ.get("HF_HOME") or REPO_ROOT / ".cache" / "huggingface-mlx-tts") / "hub"


def fetch(say: Callable[[str], object] = print) -> None:
    from huggingface_hub import snapshot_download

    say("wake-word feature models ...")
    import openwakeword.utils as owu

    owu.download_models()
    say(f"speech recognition: {WHISPER_REPO} ...")
    snapshot_download(WHISPER_REPO)
    say(f"voice: {VOICE_REPO} ...")
    snapshot_download(VOICE_REPO, cache_dir=voice_cache_dir())
    say("done. Start Julius with: python -m julius")


if __name__ == "__main__":
    if sys.argv[1:] == ["fetch"]:
        fetch()
    else:
        print("usage: python -m julius.models fetch")
