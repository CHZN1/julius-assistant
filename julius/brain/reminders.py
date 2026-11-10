"""Natural-language reminders for the model's set/list/cancel tools.

The model passes a title and a spoken time phrase; parsing, validation and the Apple save happen here, and a
reminder is only created when the person's own words asked for one."""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

DAYPARTS = {
    "morning": 9,
    "afternoon": 14,
    "evening": 18,
    "tonight": 20,
    "night": 20,
    "noon": 12,
    "midnight": 0,
}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty five": 45,
    "forty-five": 45,
    "sixty": 60,
}
INTENT = re.compile(r"\b(?:remind(?:er|ers)?|timer|alarm|nudge)\b", re.I)
CANCEL_INTENT = re.compile(r"\b(?:cancel|delete|remove|clear|never ?mind|forget)\b", re.I)


def _clock(text):
    """(hour, minute) from '3:45 pm', '3 pm', '15:30', 'noon'; None when absent."""
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)\b", text) or re.search(
        r"\b(\d{1,2}):(\d{2})\b()", text
    )
    if m:
        hour, minute, period = int(m.group(1)), int(m.group(2) or 0), m.group(3).replace(".", "")
        if minute > 59 or hour > 23 or (period and not 1 <= hour <= 12):
            raise ValueError("That is not a valid clock time.")
        if period:
            hour = hour % 12 + (12 if period == "pm" else 0)
        return hour, minute
    if re.search(r"\bnoon\b", text):
        return 12, 0
    if re.search(r"\bmidnight\b", text):
        return 0, 0
    return None


def parse_when(phrase, now, tz="America/Los_Angeles"):
    """Epoch seconds for a spoken time phrase, always in the future. Raises ValueError with a speakable reason."""
    text = re.sub(r"\s+", " ", phrase.lower().strip().strip(".,!?"))
    text = (
        re.sub(r"^(?:at|on|for)\s+", "", text)
        if not re.match(r"(?:at|on|for)\s+(?:\d|noon|midnight)", text)
        else text
    )
    zone = ZoneInfo(tz)
    local = datetime.fromtimestamp(now, zone)
    rel = re.fullmatch(
        r"in (?:about |around )?(\d+(?:\.\d+)?|half an?|an?|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty[- ]five|sixty) ?(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)(?: and a half)?",
        text,
    )
    if rel:
        raw, unit = rel.group(1), rel.group(2)
        amount = 0.5 if raw.startswith("half") else float(raw) if raw[0].isdigit() else WORDS[raw]
        if text.endswith("and a half"):
            amount += 0.5
        seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[
            unit[0] if unit[:2] != "mi" else "m"
        ] * amount
        if unit.startswith("mo"):
            raise ValueError("Please give the time in minutes, hours or days.")
        if not 0 < seconds <= 366 * 86400:
            raise ValueError("Choose a time within the next year.")
        return now + seconds
    if text in ("in half an hour", "in an hour and a half"):
        return now + (1800 if text[3:] == "half an hour" else 5400)
    day_offset, day_word = None, None
    if re.search(r"\btomorrow\b", text):
        day_offset, day_word = 1, "tomorrow"
    elif re.search(r"\b(?:today|this (?:morning|afternoon|evening)|tonight)\b", text):
        day_offset, day_word = 0, "today"
    else:
        wd = re.search(r"\b(?:next |on )?(" + "|".join(WEEKDAYS) + r")\b", text)
        if wd:
            ahead = (WEEKDAYS.index(wd.group(1)) - local.weekday()) % 7
            day_offset, day_word = ahead or 7, wd.group(1)
    clock = _clock(text)
    if clock is None:
        part = re.search(r"\b(morning|afternoon|evening|tonight|night)\b", text)
        if part:
            clock = (DAYPARTS[part.group(1)], 0)
    if clock is None and day_offset is not None:
        clock = (9, 0)  # a bare day means the start of the working day
    if clock is None:
        raise ValueError(
            "I couldn't tell what time you meant. Try something like 'at 3:45 PM' or 'in 20 minutes'."
        )
    if (
        re.search(r"\b(?:\d{1,2})(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b", text) is None
        and re.search(r"\b\d{1,2}\b(?!:)", text)
        and ":" not in text
        and not re.search(r"noon|midnight|morning|afternoon|evening|tonight|night", text)
    ):
        raise ValueError("Please say AM or PM for that time.")
    target = local.replace(tzinfo=None, hour=clock[0], minute=clock[1], second=0, microsecond=0)
    if day_offset is not None:
        target += timedelta(days=day_offset)
    due = target.replace(tzinfo=zone).timestamp()
    if day_offset in (None, 0) and due <= now:
        if day_word == "today":
            raise ValueError("That time has already passed today.")
        due = (
            (target + timedelta(days=1)).replace(tzinfo=zone).timestamp()
        )  # "at 3 pm" after 3 pm means tomorrow
    if due <= now:
        raise ValueError("That time has already passed.")
    return due


class ReminderService:
    """Create/list/cancel reminders through the same UtilitySession path as spoken commands (local plus Apple)."""

    def __init__(self, session, user_text=lambda: ""):
        self.session, self.user_text = session, user_text

    def set(self, title, when):
        if not INTENT.search(self.user_text() or ""):
            return 'I only set reminders when you ask for one. Say "remind me …" and I will.'
        try:
            due = parse_when(when, self.session.planner.clock(), self.session.timezone_name)
        except ValueError as exc:
            return str(exc)
        out = self.session._add("reminder", title.strip(), due)
        return out["data"].get("speech_message") or out["message"]

    def list(self):
        out = self.session.planner.list("reminder")
        items = out["data"].get("items") or []
        if not items:
            return "You have no active reminders."
        zone = ZoneInfo(self.session.timezone_name)
        return "\n".join(
            f"{i['title']} at "
            + datetime.fromtimestamp(i["due"], zone).strftime("%A %I:%M %p").replace(" 0", " ")
            for i in items
            if i["due"]
        )

    def cancel(self, title):
        if not CANCEL_INTENT.search(self.user_text() or ""):
            return "I only cancel reminders when you ask me to."
        out = self.session.planner.finish_named("reminder", title, "cancelled")
        if out["ok"] and self.session.provider is not None and self.session.provider.enabled:
            with self.session.planner.db() as con:
                row = con.execute(
                    "SELECT id FROM items WHERE kind='reminder' AND status='cancelled' AND lower(title)=lower(?) ORDER BY created DESC LIMIT 1",
                    (title.strip().strip('"'),),
                ).fetchone()
            if row:
                self.session.provider.cancel(row["id"])
        return f"Okay, I cancelled the reminder {title.strip()}." if out["ok"] else out["message"]
