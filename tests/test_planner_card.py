"""Planner cards: the schedule as time blocks, and a reminder's confirmation, with times in the user's zone."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from julius.brain.planning import Planner
from julius.ui_bridge import charts, planner_card
from julius.ui_bridge import events as ev
from julius.ui_bridge import panel as pn

Z = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 11, 17, 16, 20, tzinfo=Z).timestamp()


class PlannerCardTests(unittest.TestCase):
    def setUp(self):
        self.hub = pn.PanelHub(ev.EventHub())
        self.addCleanup(setattr, charts, "_panel", charts._panel)
        charts._panel = (self.hub, None, lambda: 7)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.planner = Planner(Path(self.tmp.name), clock=lambda: NOW)

    def last(self):
        return list(self.hub.cards.values())[-1]

    def test_schedule_blocks_in_local_time(self):
        start = datetime(2026, 11, 18, 11, 0, tzinfo=Z).timestamp()
        self.planner.add("event", "Studio session", start, start + 7200)
        pid = planner_card.show_schedule(
            self.planner.list()["data"]["items"], "America/Los_Angeles", NOW
        )
        self.assertTrue(pid)
        c = self.last()
        self.assertEqual(
            (c["type"], c["kind"], c["title"]), ("panel.planner", "schedule", "Tomorrow")
        )
        self.assertEqual(c["items"][0]["label"], "11:00 AM – 1:00 PM")
        self.assertEqual(c["items"][0]["end"] - c["items"][0]["start"], 7200)

    def test_a_reminder_from_the_command_shows_a_confirmation_card(self):
        from julius.agents.utility_session import UtilitySession

        UtilitySession(self.planner).handle("Remind me at 3 to send the invoice", 1)
        c = self.last()
        self.assertEqual(
            (c["kind"], c["title"], c["items"][0]["label"], c["items"][0]["day"]),
            ("reminder", "Reminder set", "3:00 PM", "Tomorrow"),
        )
        self.assertEqual(c["items"][0]["title"], "send the invoice")

    def test_validation(self):
        errors = []
        pn._v_planner({"kind": "nope", "items": []}, errors)
        self.assertEqual(len(errors), 2)

    def test_no_panel_no_card_and_no_error(self):
        charts._panel = None
        with mock.patch.object(planner_card.log, "event") as logged:
            self.assertIsNone(
                planner_card.show_schedule(
                    [{"due": NOW + 60, "title": "x"}], "America/Los_Angeles", NOW
                )
            )
        logged.assert_not_called()


if __name__ == "__main__":
    unittest.main()
