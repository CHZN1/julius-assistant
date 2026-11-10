"""Unit tests for postsoak."""

import datetime
import tempfile
import unittest
from pathlib import Path

import julius.service.postsoak as ps
import julius.ui_bridge.events as ev


class PostSoak(unittest.TestCase):
    def test_feed_check_passes_against_a_live_feed_and_fails_without(self):
        hub = ev.EventHub()
        srv = ev.EventServer(hub, port=0).start()
        hub.set_state("asleep")
        try:
            ok, detail = ps.feed_check(srv.port, timeout=5)
            self.assertTrue(ok, detail)
            self.assertIn("state=asleep", detail)
        finally:
            srv.stop()
        t = [0.0]
        ok, detail = ps.feed_check(
            1, timeout=3, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0]
        )
        self.assertFalse(ok)
        self.assertTrue(detail)

    def test_refuses_before_the_soak_ends_and_runs_after(self):
        calls = []
        d = Path(tempfile.mkdtemp()) / "r.md"
        steps = [
            ("a", lambda: (calls.append(1) or True, "out a")),
            ("b", lambda: (False, "boom b")),
        ]
        self.assertEqual(
            ps.run(now=datetime.datetime(2026, 11, 8, 23, 59), step_list=steps, results_path=d)[0],
            False,
        )
        self.assertEqual(calls, [])
        ps.DONE = Path(tempfile.mkdtemp()) / "done"
        ok, _ = ps.run(now=datetime.datetime(2026, 11, 9, 0, 15), step_list=steps, results_path=d)
        self.assertTrue(ok)
        text = d.read_text()
        self.assertIn("PASS: a", text)
        self.assertIn("FAIL: b", text)
        self.assertIn("boom b", text)
        self.assertIn("Some steps FAILED", text)
        self.assertIn("Waiting for the user", text)
        self.assertEqual(
            ps.run(now=datetime.datetime(2026, 11, 9, 0, 16), step_list=steps, results_path=d),
            (False, "already ran"),
        )

    def test_step_exceptions_are_recorded_not_raised(self):
        ps.DONE = Path(tempfile.mkdtemp()) / "done2"
        d = Path(tempfile.mkdtemp()) / "r.md"

        def bad():
            raise RuntimeError("nope")

        ps.run(now=datetime.datetime(2026, 11, 9, 1, 0), step_list=[("x", bad)], results_path=d)
        self.assertIn("RuntimeError: nope", d.read_text())

    def test_the_steps_are_exactly_the_four_requested_plus_logscan_and_never_start_the_orb(self):
        names = [n for n, _ in ps.steps()]
        self.assertEqual(
            names,
            [
                "Soak report (since the soak start)",
                "Log scan (since the soak start)",
                "Service smoke check with restart",
                "Event feed check",
                "Orb app build",
            ],
        )
        src = Path(ps.__file__).read_text()
        self.assertNotIn('"on"', src.split("def steps")[1].split("def render")[0])
        self.assertNotIn('"run"', src.split("def steps")[1].split("def render")[0])

    def test_plist_is_one_shot_on_the_right_date(self):
        d = ps.plist_dict()
        self.assertEqual(
            d["StartCalendarInterval"], {"Month": 11, "Day": 9, "Hour": 0, "Minute": 15}
        )
        self.assertFalse(d["RunAtLoad"])


if __name__ == "__main__":
    unittest.main()
