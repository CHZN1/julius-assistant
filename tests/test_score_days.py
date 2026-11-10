import unittest
from datetime import date
from julius.brain.score_days import resolve_days

THU = date(2026, 10, 29)  # a Thursday


class ScoreDayTests(unittest.TestCase):
    def d(self, phrase):
        a, b, _ = resolve_days(phrase, THU)
        return a.isoformat(), b.isoformat()

    def test_relative_days(self):
        self.assertEqual(self.d('today'), ('2026-10-29',) * 2)
        self.assertEqual(self.d('last night'), ('2026-10-28',) * 2)
        self.assertEqual(self.d('yesterday'), ('2026-10-28',) * 2)

    def test_weekdays_mean_the_most_recent_one(self):
        self.assertEqual(self.d('Sunday'), ('2026-10-25',) * 2)
        self.assertEqual(self.d('last Sunday'), ('2026-10-25',) * 2)
        self.assertEqual(self.d('Thursday'), ('2026-10-29',) * 2)
        self.assertEqual(self.d('last Thursday'), ('2026-10-22',) * 2)
        self.assertEqual(self.d('Monday night'), ('2026-10-26',) * 2)

    def test_last_week_is_a_range(self):
        self.assertEqual(self.d('last week'), ('2026-10-22', '2026-10-28'))

    def test_calendar_dates(self):
        self.assertEqual(self.d('2026-10-25'), ('2026-10-25',) * 2)
        self.assertEqual(self.d('October 25th'), ('2026-10-25',) * 2)
        self.assertEqual(self.d('Oct 4'), ('2026-10-04',) * 2)

    def test_unreadable_or_far_dates_are_refused(self):
        for phrase in ('whenever', 'next year', '2030-01-01'):
            with self.assertRaises(ValueError):
                resolve_days(phrase, THU)


if __name__ == '__main__':
    unittest.main()


class GetScoresTests(unittest.TestCase):
    def run_scores(self, day):
        from unittest.mock import patch, MagicMock
        from julius.brain import tools
        event = {"competitions": [{"competitors": [{"homeAway": "home", "team": {"displayName": "Chiefs"}, "score": "27"},
                                                    {"homeAway": "away", "team": {"displayName": "Bills"}, "score": "20"}]}],
                 "status": {"type": {"completed": True, "state": "post", "shortDetail": "Final"}}}
        response = MagicMock(); response.json.return_value = {"events": [event]}
        with patch.object(tools.requests, "get", return_value=response) as get:
            out = tools.get_scores("nfl", day)
        return out, get.call_args.kwargs["params"]["dates"]

    def test_a_single_day_sends_one_date(self):
        out, dates = self.run_scores("2026-10-25")
        self.assertEqual(dates, "20261025")
        self.assertIn("Chiefs beat Bills 27-20", out)
        self.assertIn("Sunday, October 25", out)

    def test_last_week_sends_a_range(self):
        out, dates = self.run_scores("last week")
        self.assertRegex(dates, r"^\d{8}-\d{8}$")

    def test_dispatcher_accepts_readable_days_and_refuses_the_rest(self):
        from julius.brain import tools
        from julius.brain.dispatcher import Dispatcher
        d = Dispatcher({"get_scores": lambda **kw: "ok"}, [s for s in tools.SCHEMAS if s["function"]["name"] == "get_scores"])
        self.assertTrue(d.dispatch("get_scores", {"league": "nfl", "day": "Sunday"})["ok"])
        self.assertTrue(d.dispatch("get_scores", {"league": "nhl", "day": "last night"})["ok"])
        self.assertFalse(d.dispatch("get_scores", {"league": "nhl", "day": "whenever"})["ok"])
