"""Explicit human commands own reminder/task/event writes; models get read tools."""

import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from julius.brain.conversions import local_datetime
from julius.brain.planning import output

_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
_LIST_KINDS = {
    "tasks": "task",
    "reminders": "reminder",
    "timers": "reminder",
    "calendar": "event",
    "events": "event",
}
_COMMAND_START = r"(?:remind me|(?:set|start) (?:a )?timer|add (?:a )?task|cancel (?:task|reminder|timer|event)|(?:complete|mark) (?:task|reminder)|(?:schedule|create) (?:a )?(?:calendar )?event)"


class UtilitySession:
    def __init__(self, planner, timezone_name="America/Los_Angeles", provider=None):
        self.planner = planner
        self.timezone_name = timezone_name
        self.pending = None
        self.provider = provider

    def _add(self, kind, title, due=None, end=None, zone=None):
        out = self.planner.add(kind, title, due, end)
        if out["ok"] and due is not None:
            utc = datetime.fromtimestamp(due, UTC).isoformat()
            local = datetime.fromtimestamp(due, ZoneInfo(zone or self.timezone_name)).strftime(
                "%Y-%m-%d %I:%M %p %Z"
            )
            out["message"] = out["message"].replace(utc, local)
        if (
            out["ok"]
            and kind in {"event", "reminder"}
            and self.provider is not None
            and self.provider.enabled
        ):
            external = self.provider.sync(out["data"]["id"], kind, title, due, end)
            out["data"]["apple"] = external
            out["message"] += " " + external["message"]
            if not external.get("ok"):
                out["code"] = "partial_sync"
                out["message"] += " The local item remains saved; Apple delivery is not confirmed."
        if out["ok"] and kind == "reminder" and due is not None:
            when = datetime.fromtimestamp(due, ZoneInfo(zone or self.timezone_name))
            now = datetime.fromtimestamp(self.planner.clock(), ZoneInfo(zone or self.timezone_name))
            day = (
                ""
                if when.date() == now.date()
                else " tomorrow"
                if (when.date() - now.date()).days == 1
                else when.strftime(" on %A, %B %-d")
            )
            spoken = (
                f"Okay. I'll remind you to {title} at {when.strftime('%I:%M %p').lstrip('0')}{day}."
            )
            if out["code"] == "partial_sync":
                spoken += (
                    " Saved locally, but Apple delivery is not confirmed. Details are on screen."
                )
            elif out["data"].get("apple", {}).get("ok"):
                spoken += " Saved to Apple Reminders."
            out["data"]["speech_message"] = spoken
        return out

    def handle(self, text: str, turn: int):
        """Run an explicit reminder/task/calendar command, or return None for anything else."""
        t = text.strip().rstrip(".!?")
        low = t.lower()
        pending = self.pending
        self.pending = None  # Unrelated turns cannot leave a generic yes armed.
        if pending:
            answered = self._answer_preview(pending, low, turn)
            if answered is not None:
                return answered
        try:
            for parse in (
                self._timer,
                self._relative_reminder,
                self._dated_reminder,
                self._task,
                self._listing,
                self._finish,
                self._event_preview,
            ):
                found = parse(t, low, turn)
                if found is not None:
                    return found
            if re.match(_COMMAND_START, low):
                return output(
                    False,
                    "clarification",
                    "Please specify an exact duration or ISO date/time for reminders, a task title, or an exact item reference. Calendar events need a quoted title, start and end; include a time zone when needed.",
                )
        except (ValueError, OverflowError) as exc:
            return output(False, "invalid_request", str(exc))
        return None

    def _answer_preview(self, pending: dict, low: str, turn: int):
        """A yes or no to the calendar preview shown last turn."""
        from julius.brain import confirm

        if not (
            low in {"yes", "yes please", "confirm", "cancel", "no"}
            or confirm.is_yes(low)
            or confirm.is_no(low)
        ):
            return None
        if low == "cancel" or confirm.is_no(low):
            return output(True, "cancelled", "Calendar draft discarded. No event was added.")
        if turn <= pending["turn"] or self.planner.clock() - pending["created"] > 900:
            return output(
                False, "expired", "That calendar preview has expired. Please request it again."
            )
        return self._add(
            "event", pending["title"], pending["due"], pending["end"], pending.get("zone")
        )

    def _timer(self, t, _low, _turn):
        """ "remind me in 10 minutes to X" and "set a timer for 5 minutes"."""
        match = re.fullmatch(
            r"remind me in (\d+(?:\.\d+)?) (seconds?|minutes?|hours?|days?) to (.+)", t, re.I
        ) or re.fullmatch(
            r"(?:set|start) (?:a )?timer for (\d+(?:\.\d+)?) (seconds?|minutes?|hours?)(?: (?:for|called) (.+))?",
            t,
            re.I,
        )
        if not match:
            return None
        amount, unit, title = match.groups()
        seconds = float(amount) * _UNIT_SECONDS[unit.rstrip("s").lower()]
        if not 0 < seconds <= 366 * 86400:
            raise ValueError("Choose a duration greater than zero and no more than a year.")
        return self._add("reminder", title or "Timer finished", self.planner.clock() + seconds)

    def _relative_reminder(self, t, _low, _turn):
        """ "remind me tomorrow at 9 am to X"."""
        relative = re.fullmatch(
            r"remind me (today|tomorrow) at (\d{1,2})(?::(\d{2}))?\s*(am|pm)? to (.+)", t, re.I
        )
        if not relative:
            return None
        day, hour, minute, period, title = relative.groups()
        hour = int(hour)
        minute = int(minute or 0)
        if minute > 59 or hour > 23 or period and not 1 <= hour <= 12:
            raise ValueError("Please use a valid clock time.")
        if not period and hour <= 12:
            raise ValueError("Please specify AM or PM for that clock time.")
        if period:
            hour = hour % 12 + (12 if period.lower() == "pm" else 0)
        local = datetime.fromtimestamp(self.planner.clock(), ZoneInfo(self.timezone_name))
        target = local.replace(
            tzinfo=None, hour=hour, minute=minute, second=0, microsecond=0
        ) + timedelta(days=day.lower() == "tomorrow")
        due = local_datetime(target.isoformat(), self.timezone_name).timestamp()
        return self._add("reminder", title, due)

    def _dated_reminder(self, t, _low, _turn):
        dated = re.fullmatch(r"remind me at (\S+)(?: in (\S+))? to (.+)", t, re.I)
        if not dated:
            return None
        stamp, zone, title = dated.groups()
        due = local_datetime(stamp, zone or self.timezone_name).timestamp()
        return self._add("reminder", title, due, zone=zone or self.timezone_name)

    def _task(self, t, _low, _turn):
        task = re.fullmatch(r"(?:add (?:a )?task(?: to)?|add to (?:my )?task list) (.+)", t, re.I)
        return self.planner.add("task", task.group(1)) if task else None

    def _listing(self, _t, low, _turn):
        listed = re.fullmatch(
            r"(?:list|show)(?: my)? (tasks|reminders|timers|calendar|events)", low
        )
        return self.planner.list(_LIST_KINDS[listed.group(1)]) if listed else None

    def _finish(self, t, _low, _turn):
        """Complete or cancel a task, reminder, timer or event by name."""
        finish = re.fullmatch(
            r"(complete task|mark task|complete reminder|mark reminder|cancel task|cancel reminder|cancel timer|cancel event) (.+?)(?: done)?",
            t,
            re.I,
        )
        if not finish:
            return None
        command, name = finish.groups()
        command = command.lower()
        kind = "task" if "task" in command else "event" if "event" in command else "reminder"
        status = "done" if command.startswith(("complete", "mark")) else "cancelled"
        if kind in {"event", "reminder"} and self.provider is not None and self.provider.enabled:
            remote = self._finish_remote(kind, status, name)
            if remote is not None and not remote.get("ok"):
                return remote
        return self.planner.finish_named(kind, name, status)

    def _finish_remote(self, kind: str, status: str, name: str):
        """Mirror the completion into Apple when exactly one local item matches the name."""
        with self.planner.db() as con:
            rows = con.execute(
                "SELECT id,title FROM items WHERE kind=? AND (status='pending' OR (?='done' AND kind='reminder' AND status='delivered')) LIMIT 1001",
                (kind, status),
            ).fetchall()
        wanted = name.strip('"')
        matches = [
            r for r in rows if r["id"] == wanted or r["title"].casefold() == wanted.casefold()
        ]
        if len(matches) != 1:
            return None
        act = self.provider.complete if status == "done" else self.provider.cancel
        return act(matches[0]["id"])

    def _event_preview(self, t, _low, turn):
        """ "schedule event "X" from A to B": a preview that waits for a yes."""
        event = re.fullmatch(
            r'(?:schedule|create) (?:a )?(?:calendar )?event "([^"\n]+)" from (\S+) to (\S+)(?: in (\S+))?',
            t,
            re.I,
        )
        if not event:
            return None
        title, start, end, zone = event.groups()
        zone = zone or self.timezone_name
        a, b = local_datetime(start, zone), local_datetime(end, zone)
        if (
            a.timestamp() <= self.planner.clock()
            or not a.timestamp() < b.timestamp() <= a.timestamp() + 31 * 86400
        ):
            raise ValueError("Use a future start and an end after it, within 31 days.")
        self.pending = {
            "title": title,
            "due": a.timestamp(),
            "end": b.timestamp(),
            "turn": turn,
            "created": self.planner.clock(),
            "zone": zone,
        }
        destination = (
            "Julius and the configured Apple calendar"
            if self.provider is not None and self.provider.config.get("event")
            else "Julius only; Apple Calendar is not configured"
        )
        return output(
            True,
            "preview",
            f"Calendar preview: {title}, {a.isoformat()} to {b.isoformat()}. Destination: {destination}. Say yes to confirm or cancel.",
            preview=self.pending.copy(),
        )

    def deliver_due(self, speak):
        delivered = []
        for _ in range(3):
            claimed = self.planner.claim_due(limit=1)
            if not claimed:
                break
            item = claimed[0]
            late = self.planner.clock() - item["due"] > 60
            message = ("Missed reminder: " if late else "Reminder: ") + item["title"]
            try:
                speak(message)
            except Exception:
                self.planner.retry(item["id"], item["lease"])
                raise
            if self.planner.acknowledge(item["id"], item["lease"]):
                delivered.append(item["id"])
        return delivered
