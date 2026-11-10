"""Which dates a scores request means: today, yesterday, last night, a weekday, a calendar date, or last week."""

import re
from datetime import date, datetime, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
MAX_RANGE_DAYS = 8


_RECENT = {
    "today": ("today", "tonight", "now", "right now", "this evening", "this afternoon"),
    "yesterday": ("yesterday", "last night", "yesterday evening", "yesterday night"),
}
_LAST_WEEK = (
    "last week",
    "past week",
    "this past week",
    "the past week",
    "past 7 days",
    "last 7 days",
)


def _weekday_date(text, today):
    """The date of "last Sunday", "next Friday" or a bare weekday, or None."""
    weekday = re.fullmatch(
        r"(?:(last|this past|past|this|next)\s+)?("
        + "|".join(WEEKDAYS)
        + r")(?: night| evening| afternoon)?",
        text,
    )
    if not weekday:
        return None
    which, name = weekday.groups()
    target = WEEKDAYS.index(name)
    if which == "next":
        return today + timedelta(days=(target - today.weekday()) % 7 or 7)
    back = (today.weekday() - target) % 7
    if which in ("last", "this past", "past") and back == 0:
        back = 7
    return today - timedelta(days=back)


def _calendar_date(text, today):
    """An ISO date or "March 3rd" / "3 March 2026"; ValueError when it is neither."""
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    named = re.fullmatch(
        r"(?:("
        + "|".join(m[:3] for m in MONTHS)
        + r")[a-z]*\.? (\d{1,2})|(\d{1,2}) ("
        + "|".join(m[:3] for m in MONTHS)
        + r")[a-z]*)(?:st|nd|rd|th)?(?:,? (\d{4}))?",
        re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", text),
    )
    if not named:
        raise ValueError(
            "I couldn't tell which day you meant. Try today, yesterday, a weekday like Sunday, or a date."
        )
    mon, d1, d2, mon2, year = named.groups()
    month = [m[:3] for m in MONTHS].index(mon or mon2) + 1
    day = date(int(year or today.year), month, int(d1 or d2))
    if not year and day > today + timedelta(days=14):
        day = date(day.year - 1, month, day.day)
    return day


def resolve_days(phrase, today=None):
    """(first_date, last_date, label) for a spoken day phrase; ValueError with a speakable reason when it can't be read."""
    today = today or datetime.now().date()
    text = re.sub(r"\s+", " ", str(phrase or "today").lower().strip().strip(".,!?"))
    text = re.sub(r"^(?:on|for|from|the)\s+", "", text)
    one_day = timedelta(days=1)
    if text in _RECENT["today"]:
        return today, today, "today"
    if text in _RECENT["yesterday"]:
        return today - one_day, today - one_day, "yesterday"
    if text == "tomorrow":
        return today + one_day, today + one_day, "tomorrow"
    if text in _LAST_WEEK:
        return today - timedelta(days=7), today - one_day, "the last 7 days"
    if text == "this week":
        return today - timedelta(days=today.weekday()), today, "this week"
    day = _weekday_date(text, today)
    if day is None:
        day = _calendar_date(text, today)
        if day > today + timedelta(days=14):
            raise ValueError("Scores only go about two weeks ahead.")
        if day < today - timedelta(days=366 * 3):
            raise ValueError("That is too far back for me to look up.")
    return day, day, day.strftime("%A, %B %d").replace(" 0", " ")
