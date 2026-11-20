"""Unit tests for reminders."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from julius.agents.utility_session import UtilitySession
from julius.brain.planning import Planner
from julius.brain.reminders import ReminderService, parse_when

TZ = "America/Los_Angeles"
NOW = datetime(2026, 10, 29, 14, 0, tzinfo=ZoneInfo(TZ)).timestamp()


def at(phrase):
    return datetime.fromtimestamp(parse_when(phrase, NOW, TZ), ZoneInfo(TZ)).strftime("%m-%d %H:%M")


class ParseWhenTests(unittest.TestCase):
    def test_relative(self):
        self.assertEqual(at("in 20 minutes"), "10-29 14:20")
        self.assertEqual(at("in an hour"), "10-29 15:00")
        self.assertEqual(at("in half an hour"), "10-29 14:30")

    def test_clock_times(self):
        self.assertEqual(at("at 3:45 PM"), "10-29 15:45")
        self.assertEqual(at("at 1 pm"), "10-30 13:00")  # already passed today: next occurrence
        self.assertEqual(at("5 pm tomorrow"), "10-30 17:00")

    def test_day_parts_and_weekdays(self):
        self.assertEqual(at("tomorrow morning"), "10-30 09:00")
        self.assertEqual(at("tomorrow evening"), "10-30 18:00")
        self.assertEqual(at("tonight"), "10-29 20:00")
        self.assertEqual(at("friday at 9am"), "10-30 09:00")

    def test_unclear_or_past_times_are_refused(self):
        for phrase in ("whenever", "at 3", "this morning"):
            with self.assertRaises(ValueError):
                parse_when(phrase, NOW, TZ)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.planner = (
            Planner(Path(folder), clock=lambda: NOW)
            if "clock" in Planner.__init__.__code__.co_varnames
            else Planner(Path(folder))
        )
        self.planner.clock = lambda: NOW
        self.text = "remind me to call mom at 3:45 PM"
        self.service = ReminderService(UtilitySession(self.planner, TZ), lambda: self.text)

    def test_set_list_cancel(self):
        spoken = self.service.set("call mom", "at 3:45 PM")
        self.assertIn("call mom", spoken)
        self.assertIn("3:45", spoken)
        self.assertIn("call mom", self.service.list())
        self.text = "cancel my call mom reminder"
        self.assertIn("cancelled", self.service.cancel("call mom"))
        self.assertEqual(self.service.list(), "You have no active reminders.")

    def test_requires_the_person_to_ask(self):
        self.text = "what is on my calendar"
        self.assertIn("only set reminders", self.service.set("x", "in 5 minutes"))
        self.assertEqual(self.service.list(), "You have no active reminders.")

    def test_bad_time_is_explained(self):
        self.assertIn("couldn't tell", self.service.set("call mom", "whenever"))


if __name__ == "__main__":
    unittest.main()


class ClockTimeWithoutDayTests(unittest.TestCase):
    """ "Remind me at 3 to send the invoice": no day and no AM/PM (a live demo turn answered with a raw "Invalid isoformat string")."""

    def setUp(self):
        import tempfile
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from julius.agents.utility_session import UtilitySession

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.z = ZoneInfo("America/Los_Angeles")
        self.now = datetime(2026, 11, 17, 16, 20, tzinfo=self.z).timestamp()  # 4:20 PM
        self.planner = Planner(Path(self.tmp.name), clock=lambda: self.now)
        self.u = UtilitySession(self.planner)

    def due(self):
        from datetime import datetime

        item = self.planner.list()["data"]["items"][-1]
        return datetime.fromtimestamp(item["due"], self.z)

    def test_at_3_means_3_pm_tomorrow_once_it_has_passed(self):
        r = self.u.handle("Remind me at 3 to send the invoice", 1)
        self.assertTrue(r["ok"], r)
        self.assertEqual((self.due().day, self.due().hour), (18, 15))
        self.assertIn("3:00 PM tomorrow", r["data"]["speech_message"])

    def test_at_5_pm_is_still_today(self):
        self.assertTrue(self.u.handle("remind me at 5 pm to call Sam", 1)["ok"])
        self.assertEqual((self.due().day, self.due().hour), (17, 17))

    def test_at_9_needs_am_or_pm(self):
        r = self.u.handle("remind me at 9 to call Sam", 1)
        self.assertFalse(r["ok"])
        self.assertIn("AM or PM", r["message"])

    def test_an_unreadable_time_is_a_sentence_not_an_exception(self):
        r = self.u.handle("remind me at noonish to eat", 1)
        self.assertFalse(r["ok"])
        self.assertNotIn("isoformat", r["message"])

    def test_the_list_shows_local_times(self):
        self.u.handle("remind me at 5 pm to call Sam", 1)
        msg = self.planner.list(tz="America/Los_Angeles")["message"]
        self.assertIn("5:00 PM PST", msg)
        self.assertNotIn("+00:00", msg)
        self.assertTrue(
            msg.startswith("Today is "), msg
        )  # "tomorrow" needs no second call for the date
