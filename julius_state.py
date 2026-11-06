"""Small persistent state that must survive a restart: quiet mode (detector off until you wake Julius by hotkey)."""
import os
from pathlib import Path
def _flag(): return Path(os.environ.get("JULIUS_STATE_DIR", "~/.julius")).expanduser() / "quiet"
def is_quiet(): return _flag().exists()
def set_quiet(on):
    f = _flag()
    if on: f.parent.mkdir(parents=True, exist_ok=True); f.write_text("1")
    elif f.exists(): f.unlink()
