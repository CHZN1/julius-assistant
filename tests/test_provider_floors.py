"""run_all --provider judges every suite by its floor; a missing floor or a missing score fails instead of passing."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import run_all


def row(score="x"):
    return {"suite": "s", "score": score, "detail": "", "why": "", "regressed": False}


class Floors(unittest.TestCase):
    def test_live_data_below_its_floor_fails(self):
        r = run_all.apply_floors(row(), "livedata", {"ok_pct": 64.3, "provider": "ollama:m"})
        self.assertFalse(r["floor_ok"])
        self.assertIn("ok_pct 64.3 < 100.0", r["why"])
        self.assertTrue(r["regressed"])
        self.assertTrue(run_all.apply_floors(row(), "livedata", {"ok_pct": 100.0})["floor_ok"])

    def test_routing_needs_both_its_score_and_valid_tool_json(self):
        good = {"ok_pct": 96.0, "tool_json_valid_pct": 100.0, "tool_calls_counted": 10}
        self.assertTrue(run_all.apply_floors(row(), "routing", good)["floor_ok"])
        bad_json = {**good, "tool_json_valid_pct": 98.0}
        self.assertIn(
            "tool_json_valid_pct 98.0 < 99.0",
            run_all.apply_floors(row(), "routing", bad_json)["why"],
        )
        self.assertFalse(
            run_all.apply_floors(row(), "routing", {**good, "ok_pct": 94.9})["floor_ok"]
        )
        self.assertTrue(
            run_all.apply_floors(row(), "routing", {"ok_pct": 100.0})["floor_ok"]
        )  # Anthropic: no JSON count

    def test_nothing_passes_by_omission(self):
        self.assertIn("no ok_pct in the results", run_all.apply_floors(row(), "memory", {})["why"])
        self.assertIn(
            "no floor defined", run_all.apply_floors(row(), "vision", {"ok_pct": 100})["why"]
        )
        self.assertIn(
            "did not run", run_all.apply_floors(row("DID NOT RUN"), "file", {"ok_pct": 100})["why"]
        )
        for prefix in ("routing", "file", "livedata", "memory", "planner", "research"):
            self.assertIn(prefix, run_all.FLOORS)


if __name__ == "__main__":
    unittest.main()
