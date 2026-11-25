"""The voice replay eval is judged against a stored baseline (fixed takes, hint and seed), not against whatever the previous run happened to hear."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import replay_frontend as rf
import run_all


def row(sid, turn, heard, wer):
    return {
        "scenario": sid,
        "turn": turn,
        "say": "x",
        "heard": heard,
        "wer": wer,
        "utterances": 1,
        "expected_utterances": 1,
        "self_heard": 0,
        "ignored": False,
    }


BASE = {"mean_wer": 0.25, "rows": [row("s01", 0, "hello", 0.0), row("s01", 1, "status", 0.5)]}


class Baseline(unittest.TestCase):
    def test_same_transcripts_are_not_a_regression(self):
        regressed, why, changed = rf.compare(BASE["rows"], BASE)
        self.assertEqual((regressed, why, changed), (False, [], []))

    def test_a_worse_mean_or_a_lost_exact_turn_is(self):
        rows = [row("s01", 0, "hello there", 0.5), row("s01", 1, "status", 0.5)]
        regressed, why, changed = rf.compare(rows, BASE)
        self.assertTrue(regressed)
        self.assertIn("WER 0.25 -> 0.5", why[0])
        self.assertIn("no longer exact: s01#1", why[1])
        self.assertEqual(changed, ["s01#1"])

    def test_a_better_score_is_reported_but_not_a_regression(self):
        rows = [row("s01", 0, "hello", 0.0), row("s01", 1, "the status", 0.0)]
        regressed, _why, changed = rf.compare(rows, BASE)
        self.assertFalse(regressed)
        self.assertEqual(changed, ["s01#2"])

    def test_takes_come_from_the_baseline_and_a_missing_one_is_named(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "day" / "s01_x__take2" / "rec").mkdir(parents=True)
            (Path(d) / "day" / "s01_x__take2" / "rec" / "events.jsonl").write_text("")
            takes, missing = rf.baseline_takes(
                {"takes": ["day/s01_x__take2", "day/s02_y__take1"]}, d
            )
        self.assertEqual(list(takes), ["s01_x"])
        self.assertEqual(missing, ["day/s02_y__take1"])

    def test_run_all_uses_the_baseline_verdict_not_the_previous_run(self):
        summaries = iter(
            [
                (Path("old"), {"mean_wer": 0.10}),
                (Path("new"), {"mean_wer": 0.43, "baseline_regression": False}),
            ]
        )
        with (
            mock.patch.object(run_all, "latest_summary", side_effect=lambda p: next(summaries)),
            mock.patch.object(run_all, "run", return_value=(SimpleNamespace(returncode=0), 1.0)),
        ):
            r = run_all.suite(
                "voice front-end replay",
                "voice_eval.py",
                [],
                "voicereplay_gate",
                "mean_wer",
                "WER",
                3.0,
                False,
            )
        self.assertFalse(
            r["regressed"]
        )  # 0.10 -> 0.43 against the last run, but the baseline says ok


if __name__ == "__main__":
    unittest.main()
