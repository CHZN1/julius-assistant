"""Low-confidence confirmation (Phase F3c): when recognition was shaky AND the words ask for something that changes things, Julius reads back what he heard
("Did you say: trash the old reports?") and acts only on a yes, instead of guessing. Harmless requests (questions, chat) are never interrupted.

    gate = LowConfidenceGate()
    action, text, ask = gate.step(text, conf, voice=True)
        action "run"   process `text` (the original words after a yes, or this turn's words unchanged)
        action "ask"   say `ask` and wait; the next turn's yes/no resolves it
        action "drop"  the answer was no: say `ask` (an acknowledgement) and do nothing

`conf` is the recognizer's _last_asr_confidence dict: worst segment avg_logprob below LOGPROB (closer to 0 is surer) or no-speech probability above NO_SPEECH is shaky."""

import re
import time
from collections.abc import Callable
from typing import Any

from julius.brain import confirm

LOGPROB = -0.9
NO_SPEECH = 0.35
EXPIRES = 45.0
RISKY = re.compile(
    r"\b(trash|delete|remove|erase|wipe|move|rename|merge|discard|forget|empty|overwrite|clean ?up|cancel|undo|send|email|text|call|message|post|start the coding|have the coding agent|run the coding)\b",
    re.I,
)


def is_shaky(
    conf: dict[str, Any] | None,
    logprob: float = LOGPROB,
    no_speech: float = NO_SPEECH,
) -> bool:
    c = conf or {}
    if c.get("rejected"):
        return False
    lp = [x for x in (c.get("segment_log_probabilities") or []) if x is not None]
    ns = c.get("no_speech_probability")
    return bool((lp and min(lp) < logprob) or (ns is not None and ns > no_speech))


def is_risky(text: str) -> bool:
    return bool(RISKY.search(text or ""))


class LowConfidenceGate:
    def __init__(
        self,
        logprob: float = LOGPROB,
        no_speech: float = NO_SPEECH,
        expires: int | float = EXPIRES,
        clock: Callable = time.time,
        enabled: bool = True,
    ) -> None:
        self.logprob, self.no_speech, self.expires, self.clock, self.enabled = (
            logprob,
            no_speech,
            expires,
            clock,
            enabled,
        )
        self.pending = None
        self._at = 0.0
        self.asked = 0

    def step(
        self, text: str, conf: dict[str, list[float] | float], voice: bool = True
    ) -> tuple[str, str, None] | tuple[str, str, str]:
        if self.pending is not None and self.clock() - self._at > self.expires:
            self.pending = None
        if self.pending is not None:
            original, self.pending = self.pending, None
            if confirm.is_yes(text):
                return "run", original, None
            if confirm.is_no(text):
                return "drop", text, "Okay, I'll leave it."
            # something else entirely: the new words stand on their own (and go through the same check)
        if (
            self.enabled
            and voice
            and is_risky(text)
            and is_shaky(conf, self.logprob, self.no_speech)
        ):
            self.pending, self._at = text, self.clock()
            self.asked += 1
            return (
                "ask",
                text,
                f"I'm not sure I heard that right. Did you say: {text.strip().rstrip('.?!')}?",
            )
        return "run", text, None
