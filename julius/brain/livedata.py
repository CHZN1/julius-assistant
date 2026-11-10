"""No made-up live data (Phase F3f).

A question about something that changes (scores, prices, schedules, weather, news, "right now") is answered from a tool result or not at all. The brain is told so in its
prompt, and this module is the backstop: while a reply to such a question is being spoken and no tool has returned anything this turn, any sentence that states a number,
time or amount is dropped before it reaches the speaker, and a single honest line is spoken instead.

    is_live_query(text)            True for questions whose answer changes over time
    states_a_figure(sentence)      True for a sentence with a score, price, time, count or percentage in it
    UNGROUNDED_LINE                what he says instead
"""

import re
from typing import Any

LIVE_QUERY = re.compile(
    r"\b(?:scores?|final score|who (?:won|wins|is winning|is playing|plays|did .{0,30} beat)|did .{0,30} (?:win|lose)|results?|standings?|"
    r"prices?|how much (?:is|are|does|do|did)|cost of|costs?|trading|stocks?|share price|quotes?|odds|spread|over.under|"
    r"schedules?|(?:what time|when) (?:does|do|is|are|will)\b.{0,40}\b(?:game|match|start|play|kick|tip|puck)|game (?:tonight|today|tomorrow)|plays? tonight|"
    r"news|headlines?|weather|forecast|temperature|how (?:hot|cold|warm)|rain(?:ing|y)?|traffic|"
    r"right now|currently|latest|as of now|at the moment|this (?:morning|afternoon|evening)|tonight|today.s)\b",
    re.I,
)

FIGURE = re.compile(
    r"\$\s?\d|\b\d+(?:[.,]\d+)?\s?(?:dollars?|cents?|percent|degrees|points?|goals?|runs?|yards?|touchdowns?|games?|minutes?|hours?|am|pm|a\.m\.|p\.m\.)\b|"
    r"\b\d{1,2}:\d\d\b|\b\d{1,3}\s?(?:-|to)\s?\d{1,3}\b|\b\d+(?:[.,]\d+)?\b|"
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million)\b"
    r"(?:\s+(?:dollars?|cents?|percent|degrees|points?|goals?|to\s+\w+|\w+\s+(?:am|pm)))",
    re.I,
)

UNGROUNDED_LINE = "I can't look that up right now, so I won't guess."


def is_live_query(text: str) -> bool:
    return bool(LIVE_QUERY.search(text or ""))


def states_a_figure(sentence: str) -> bool:
    return bool(FIGURE.search(sentence or ""))


def blocked(sentence: str, live_query: bool, tool_result_this_turn: bool) -> bool:
    """Should this sentence be withheld? Only for live questions with nothing from a tool behind the answer yet."""
    return bool(live_query and not tool_result_this_turn and states_a_figure(sentence))


def tool_result_this_turn(
    messages: list[Any],
) -> bool:
    """True when a tool result has come back since the user's latest message."""
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    return any(m.get("role") == "tool" for m in messages[last_user + 1 :])
