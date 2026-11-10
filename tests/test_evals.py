"""The eval tooling itself: case files are well-formed, scoring and regression flags behave."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import audio_regression as ar
import common
import routing_eval


class EvalToolingTests(unittest.TestCase):
    def test_routing_cases_are_well_formed(self):
        cases = routing_eval.load_cases()
        self.assertGreaterEqual(len(cases), 100)
        self.assertEqual(len({c["id"] for c in cases}), len(cases))
        for c in cases:
            self.assertIn(c["expect"]["path"], {"shortcut", "direct", "tool", "escalate"}, c["id"])
            self.assertTrue(c["text"].strip())
            for p in c["expect"].get("path_in", []):
                self.assertIn(p, {"shortcut", "tool", "direct", "escalate"})
        self.assertEqual(
            {c["expect"]["path"] for c in cases}, {"shortcut", "direct", "tool", "escalate"}
        )

    def test_audio_cases_cover_the_twenty_recorded_phrases(self):
        self.assertEqual(set(ar.CASES["recorded"]), {str(i) for i in range(1, 21)})
        self.assertEqual(set(ar.CASES["keywords"]), set(ar.CASES["recorded"]))

    def test_a_company_name_counts_as_its_ticker_in_price_arguments(self):
        self.assertTrue(routing_eval.arg_matches("tsla", '{"symbol": "Tesla"}'))
        self.assertTrue(routing_eval.arg_matches("btc", '{"symbol": "Bitcoin"}'))
        self.assertTrue(routing_eval.arg_matches("tsla", '{"symbol": "TSLA"}'))
        self.assertFalse(routing_eval.arg_matches("tsla", '{"symbol": "Apple"}'))
        self.assertFalse(routing_eval.arg_matches("btc", "not json"))

    def test_judge_scores_path_tool_args_and_success(self):
        case = {
            "expect": {"path": "shortcut", "tool": "get_weather", "args_contain": ["springfield"]}
        }
        self.assertEqual(
            routing_eval.judge(
                case,
                {"path": "shortcut", "tool": "get_weather", "args": '{"location": "Springfield"}'},
            ),
            (True, True, True, True),
        )
        self.assertEqual(
            routing_eval.judge(
                case, {"path": "tool", "tool": "get_weather", "args": "springfield"}
            )[0],
            False,
        )
        self.assertEqual(
            routing_eval.judge(
                case, {"path": "shortcut", "tool": "get_forecast", "args": "springfield"}
            )[1],
            False,
        )
        either = {
            "expect": {"path": "shortcut", "path_in": ["shortcut", "tool"], "tool": "get_quote"}
        }
        self.assertTrue(
            routing_eval.judge(either, {"path": "tool", "tool": "get_quote", "args": ""})[0]
        )
        direct = {"expect": {"path": "direct"}}
        self.assertEqual(
            routing_eval.judge(direct, {"path": "direct", "tool": ""})[:2], (True, True)
        )
        self.assertFalse(
            routing_eval.judge(
                {"expect": {"path": "shortcut", "ok": True}},
                {"path": "shortcut", "tool": "utility", "ok": False},
            )[3]
        )

    def test_wer_and_missed_terms(self):
        self.assertEqual(
            ar.wer("the NHL score", "the n h l score"), 0
        )  # spelled-out acronyms are normalised
        self.assertGreater(ar.wer("hello world", "hello there"), 0)
        self.assertEqual(ar.wer("hello world", "hello world"), 0)
        self.assertEqual(ar.missed(5, "the best prize picks slip"), [])
        self.assertEqual(ar.missed(5, "the best price big slip"), ["acmecorp"])
        self.assertEqual(
            ar.missed(8, "what is seventeen percent of two thousand four hundred eighty"), []
        )

    def test_regression_flags_compare_with_previous_run(self):
        with tempfile.TemporaryDirectory() as d:
            previous = Path(d) / "x_1.csv"
            previous.write_text("id,ok\na,True\nb,True\nc,False\n")
            broke, fixed, name = common.regressions(
                [{"id": "a", "ok": True}, {"id": "b", "ok": False}, {"id": "c", "ok": True}],
                previous,
            )
            self.assertEqual((broke, fixed, name), (["b"], ["c"], "x_1.csv"))
            self.assertEqual(common.regressions([], None)[2], "no previous run")


if __name__ == "__main__":
    unittest.main()


class FileEvalTests(unittest.TestCase):
    def test_file_eval_scenarios_pass_in_a_sandbox(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
        import file_eval

        for sid, name, fn in file_eval.SCENARIOS:
            env = file_eval.Env()
            try:
                ok, detail = fn(env)
            finally:
                import shutil

                shutil.rmtree(env.dir, ignore_errors=True)
            self.assertTrue(ok, f"{sid} {name}: {detail}")
