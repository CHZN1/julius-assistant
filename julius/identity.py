"""The assistant's name, in one place: `[identity]` in julius.toml (defaults: everything is "Julius").

    [identity]
    name = "Julius"           # what it calls itself: prompts, spoken replies, cards, console lines
    display_name = "Julius"   # what a UI shows (title, the speaker label); defaults to name
    wake_word = "Julius"      # what wakes it: the Whisper wake check and its vocabulary hint; defaults to name

Changing these renames the assistant everywhere it talks about itself. Three things do not follow automatically:
- The trained wake-word model (julius/wake/models/<wake word>_starter.onnx, used by `[wake] engine = "cascade"`) recognises one
  word. A new wake word needs its own model: build a dataset and train it (`python -m julius.wake.build_dataset`, then
  `python -m julius.wake.train_head`). Until then the cascade says so at start-up and falls back to the Whisper wake, which
  already listens for the new word (it transcribes more audio, so it costs more CPU). The personal model learned from your
  voice is kept per wake word (~/.julius/wake_personal for Julius, wake_personal_<word> for another).
- Infrastructure names stay as they are, so renaming never moves your data: ~/.julius, ~/Library/Logs/Julius, "~/Julius Saved",
  the launchd label com.julius.assistant and the `julius` package.
- Two extras only fit the default name and switch off for any other: the persona's namesake line ("Named after Julius
  Novochrono") and the sound-alikes Whisper confuses with "Julius" (Juliet as a wake; Julia, Julian, Julie, Jules in the hint).
  Set `namesake` or `wake_aliases` under [identity] to give a new name its own.

Text that mentions the name is written with tokens and filled by `fill`: {name}, {display_name}, {wake_word}, {namesake_sentence}.
Plain str.replace, never str.format, so other braces in a prompt (JSON examples) are left alone."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Any

DEFAULT_NAME = "Julius"
DEFAULT_NAMESAKE = "Julius Novochrono"
DEFAULT_WAKE_ALIASES = (
    "Juliet",
)  # Whisper writes a spoken "Julius" as "Juliet" often enough to count it as a wake
DEFAULT_WAKE_CONTRAST = "Julius. Hey Julius. Julia. Julian. Julie. Jules."  # the name next to its sound-alikes (see config.asr_initial_prompt)


@dataclass(frozen=True)
class Identity:
    name: str = DEFAULT_NAME
    display_name: str = ""
    wake_word: str = ""
    namesake: str | None = None  # None: the default name's namesake, nobody for any other name
    wake_aliases: tuple[str, ...] | None = (
        None  # None: the default name's sound-alikes, none for any other name
    )

    def __post_init__(self) -> None:
        name = (self.name or DEFAULT_NAME).strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z' -]{0,30}", name):
            raise ValueError("identity.name must be a short name in letters, for example Julius")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "display_name", (self.display_name or name).strip())
        wake = (self.wake_word or name).strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z' -]{0,30}", wake):
            raise ValueError(
                "identity.wake_word must be a short word in letters, for example Julius"
            )
        object.__setattr__(self, "wake_word", wake)
        is_default = name == DEFAULT_NAME
        if self.namesake is None:
            object.__setattr__(self, "namesake", DEFAULT_NAMESAKE if is_default else "")
        if self.wake_aliases is None:
            object.__setattr__(
                self, "wake_aliases", DEFAULT_WAKE_ALIASES if wake == DEFAULT_NAME else ()
            )
        else:
            object.__setattr__(self, "wake_aliases", tuple(self.wake_aliases))

    @property
    def wake_contrast(self) -> str:
        """The start of the wake-check hint: the wake word next to what Whisper confuses it with."""
        if self.wake_word == DEFAULT_NAME:
            return DEFAULT_WAKE_CONTRAST
        return f"{self.wake_word}. Hey {self.wake_word}."

    @property
    def wake_words(self) -> tuple[str, ...]:
        """Every spelling that counts as the wake word in a transcript."""
        return (self.wake_word, *self.wake_aliases)

    @property
    def model_stem(self) -> str:
        """The wake-word model files: julius/wake/models/<stem>_starter.onnx."""
        return re.sub(r"[^a-z0-9]+", "_", self.wake_word.lower()).strip("_")

    @property
    def namesake_sentence(self) -> str:
        if not self.namesake:
            return ""
        return f"Named after {self.namesake} — and you share his temperament. "

    def fill(self, text: str) -> str:
        return (
            text.replace("{namesake_sentence}", self.namesake_sentence)
            .replace("{display_name}", self.display_name)
            .replace("{wake_word}", self.wake_word)
            .replace("{name}", self.name)
        )


def from_config(cfg: dict[str, Any]) -> Identity:
    raw = dict(cfg.get("identity") or {})
    aliases = raw.get("wake_aliases")
    return Identity(
        name=str(raw.get("name") or DEFAULT_NAME),
        display_name=str(raw.get("display_name") or ""),
        wake_word=str(raw.get("wake_word") or ""),
        namesake=None if raw.get("namesake") is None else str(raw["namesake"]),
        wake_aliases=None if aliases is None else tuple(str(a) for a in aliases),
    )


@functools.lru_cache(maxsize=1)
def current() -> Identity:
    """The identity from julius.toml (and julius.local.toml), read once per process."""
    from julius import config

    try:
        return from_config(config.load_config())
    except FileNotFoundError:
        return Identity()


def reset() -> None:
    """Forget the cached identity (tests, or after editing julius.toml in a running process)."""
    current.cache_clear()


TEMPLATES: list[
    str
] = []  # every text filled with the configured identity, unfilled (tests render them under other names)


def fill(text: str, ident: Identity | None = None) -> str:
    """`text` with its name tokens filled from `ident` (default: the configured identity, and the template is kept in TEMPLATES)."""
    if ident is None and text not in TEMPLATES:
        TEMPLATES.append(text)
    return (ident or current()).fill(text)


def name() -> str:
    return current().name


def display_name() -> str:
    return current().display_name


def wake_word() -> str:
    return current().wake_word
