"""Planner cards for the side panel: the schedule ("what's on my calendar tomorrow") as time blocks, and a new reminder's confirmation.
Times are formatted here in the user's time zone, so the panel never guesses one. Cards are extra: a failure here never touches the answer."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from julius import log

MAX_ITEMS = 12


def _panel() -> Any:
    from julius.ui_bridge import charts

    return charts._panel


def _day(d: datetime, now: datetime) -> str:
    n = (d.date() - now.date()).days
    return "Today" if n == 0 else "Tomorrow" if n == 1 else d.strftime("%A, %B %-d")


def _clock(d: datetime) -> str:
    return d.strftime("%-I:%M %p")


def schedule_items(items: list[dict], tz: str, now_ts: float) -> list[dict]:
    """Planner rows -> card items for the next days, soonest first: {day, start, end, label, title, kind} (start/end are Unix seconds)."""
    z = ZoneInfo(tz)
    now = datetime.fromtimestamp(now_ts, z)
    out = []
    for r in sorted(
        (r for r in items if r.get("due") and r["due"] >= now_ts - 3600), key=lambda r: r["due"]
    )[:MAX_ITEMS]:
        a = datetime.fromtimestamp(r["due"], z)
        label = _clock(a) + (
            f" – {_clock(datetime.fromtimestamp(r['end'], z))}" if r.get("end") else ""
        )
        out.append(
            {
                "day": _day(a, now),
                "start": float(r["due"]),
                "end": float(r["end"]) if r.get("end") else None,
                "label": label,
                "title": str(r.get("title") or ""),
                "kind": str(r.get("kind") or "event"),
            }
        )
    return out


def show_schedule(items: list[dict], tz: str, now_ts: float) -> str | None:
    p = _panel()
    if p is None:
        return None
    try:
        panel, _files, turn_fn = p
        rows = schedule_items(items, tz, now_ts)
        if not rows:
            return None
        days = list(dict.fromkeys(r["day"] for r in rows))
        title = days[0] if len(days) == 1 else "Coming up"
        return panel.planner(
            {"title": title, "kind": "schedule", "items": rows, "note": f"Times in {tz}"},
            turn=turn_fn(),
        )
    except Exception as exc:  # the card is extra
        log.event("planner_card", error=type(exc).__name__)
        return None


def show_reminder(title: str, due: float, tz: str, now_ts: float, note: str = "") -> str | None:
    p = _panel()
    if p is None:
        return None
    try:
        panel, _files, turn_fn = p
        z = ZoneInfo(tz)
        a = datetime.fromtimestamp(due, z)
        item = {
            "day": _day(a, datetime.fromtimestamp(now_ts, z)),
            "start": float(due),
            "end": None,
            "label": _clock(a),
            "title": title,
            "kind": "reminder",
        }
        return panel.planner(
            {"title": "Reminder set", "kind": "reminder", "items": [item], "note": note},
            turn=turn_fn(),
        )
    except Exception as exc:
        log.event("planner_card", error=type(exc).__name__)
        return None
