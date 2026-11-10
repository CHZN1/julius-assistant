"""Selected seed-1301 speaker and signals for consistent reply delivery."""

import re
from collections.abc import Iterator
from unittest.mock import Mock

from julius.paths import REPO_ROOT

ROOT = REPO_ROOT
REF_TEXT = "Hey, Roy. Yeah, I'm here. Oh — wait, that's actually fascinating! How did you get that working? Ha, okay, I really like that. But seriously, there is one thing we should think through before we change anything. Alright. Show me what you've got."
BALANCED_REFERENCE = ROOT / "julius_1301_tuning/1301_a_balanced.wav"
LIFT_REFERENCE = ROOT / "julius_1301_tuning/1301_b_more_lift.wav"
TTS_LIFT_HINT = re.compile(
    r"[!?]|\b(oh|wow|whoa|wait|interesting|fascinating|cool|awesome|love|like that|"
    r"nice|great|amazing|curious|wonder|excited|exactly|there we go|got it)\b",
    re.I,
)

REPLY_LIFT_HINT = re.compile(
    r"!|\b(wow|whoa|interesting|fascinating|cool|awesome|love that|like that|"
    r"nice|great|amazing|curious|wonder|excited|there we go|ha)\b",
    re.I,
)


def delivery_mode(text: str) -> str:
    # Questions alone do not imply excitement. Keep the original hint available
    # for historical comparison, but route current delivery by expressed energy.
    return "lift" if REPLY_LIFT_HINT.search(text) else "balanced"


def numerical_failure(exc: RuntimeError | FloatingPointError) -> bool:
    if isinstance(exc, FloatingPointError):
        return True
    message = str(exc).lower()
    return any(
        term in message
        for term in (
            "probability tensor contains",
            "invalid multinomial distribution",
            "nonfinite",
            "non-finite",
        )
    ) or bool(re.search(r"\b(nan|infinity)\b", message))


def configure_qwen_decoder(model: Mock) -> None:
    """Keep native decoder context while bounding each activation allocation."""
    from functools import partial

    decoder = model.model.speech_tokenizer.model.decoder
    decoder.chunked_decode = partial(decoder.chunked_decode, chunk_size=96, left_context_size=25)


def speech_chunks(text: str, max_words: int = 12) -> Iterator[str]:
    """Bound generation spans, preferring clause boundaries; preserve all words."""
    words = text.split()
    while words:
        count = min(max_words, len(words))
        if len(words) > max_words:
            boundaries = [
                i + 1
                for i, word in enumerate(words[:max_words])
                if i >= 3 and word.endswith((",", ";", ":"))
            ]
            if boundaries:
                count = boundaries[-1]
        yield " ".join(words[:count])
        words = words[count:]
