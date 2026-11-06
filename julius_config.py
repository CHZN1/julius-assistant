"""Runtime configuration: julius.toml defaults, environment overrides, and the .env API key.

The key is read from the environment or the project's .env file and is never printed or logged;
use describe_key() for a safe status line.
"""
import os, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULTS = {
    "brain": {"mode": "cloud"},
    "models": {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5"},
    "spend": {"daily_cap_usd": 5.0, "offline_at_multiple": 2.0},
    "orchestrator": {"max_steps": 8, "subagent_max_steps": 6},
    "offline": {"model": "llama3.2:3b", "retry_seconds": 60},
    "voice": {"keep_loaded": True},
    "barge_in": {"hotkey": "<ctrl>+<alt>+s", "voice": False},
    "wake": {"engine": "whisper", "threshold_low": 0.05, "speaker_threshold": 0.25, "verifier": "~/.julius/wake_verifier.pkl"},
    "asr": {"vocabulary_hint": True,
            "vocabulary": ["Julius", "AcmeCorp", "Pat Example", "FER 2013", "Ethereum", "ETH", "Bitcoin", "NHL", "NFL", "MLB", "WNBA", "Springfield", "Shelbyville"],
            "nhl_teams": ["Ducks", "Coyotes", "Bruins", "Sabres", "Flames", "Hurricanes", "Blackhawks", "Avalanche", "Blue Jackets", "Stars", "Red Wings", "Oilers", "Panthers", "Kings", "Wild",
                          "Canadiens", "Predators", "Devils", "Islanders", "Rangers", "Senators", "Flyers", "Penguins", "Sharks", "Kraken", "Blues", "Lightning", "Maple Leafs", "Utah Mammoth",
                          "Canucks", "Golden Knights", "Capitals", "Jets"],
            "nfl_teams": ["Cardinals", "Falcons", "Ravens", "Bills", "Panthers", "Bears", "Bengals", "Browns", "Cowboys", "Broncos", "Lions", "Packers", "Texans", "Colts", "Jaguars", "Chiefs",
                          "Raiders", "Chargers", "Rams", "Dolphins", "Vikings", "Patriots", "Saints", "Giants", "Jets", "Eagles", "Steelers", "49ers", "Seahawks", "Buccaneers", "Titans", "Commanders"]},
}


def load_dotenv(path=None):
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


def load_config(path=None):
    """julius.toml merged over DEFAULTS section by section. JULIUS_BRAIN overrides brain.mode."""
    config = {section: dict(values) for section, values in DEFAULTS.items()}
    path = Path(path or os.environ.get("JULIUS_CONFIG") or ROOT / "julius.toml")
    if path.is_file():
        with path.open("rb") as handle:
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
    return config


def api_key():
    load_dotenv()
    return os.environ.get("ANTHROPIC_API_KEY") or None


def describe_key():
    """Safe status for logs: whether a key is present and how long it is, never the value."""
    key = api_key()
    return f"set ({len(key)} chars)" if key else "missing"


WAKE_CONTRAST = "Julius. Hey Julius. Julia. Julian. Julie. Jules."


def asr_initial_prompt(config=None, wake=False):
    """The Whisper initial_prompt built from [asr] in julius.toml, or None when the hint is disabled.

    wake=False (default): the vocabulary only, for transcribing a request while Julius is awake.
    wake=True: for the utterance that may contain the wake word. It opens with the wake word next to its sound-alikes and leaves
    the bare name out of the vocabulary list, because a vocabulary hint that lists "Julius" alone makes Whisper hear Julia and
    Julian as Julius (a false wake). Only the wake check uses it (evals/wake_hint_study.py)."""
    asr = (config or load_config())["asr"]
    if not asr.get("vocabulary_hint", True):
        return None
    words = list(asr.get("vocabulary", [])) + list(asr.get("nhl_teams", [])) + list(asr.get("nfl_teams", []))
    if not wake:
        return ", ".join(dict.fromkeys(words)) + "." if words else None
    words = [w for w in words if w.strip().lower() != "julius"]
    return WAKE_CONTRAST + " " + ", ".join(dict.fromkeys(words)) + "." if words else None
