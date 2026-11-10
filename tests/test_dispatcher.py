import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import julius.brain.tools as tools
from julius.agents.file_agent import FileAgent
from julius.agents.file_session import FileSession
from julius.brain.calculator import evaluate
from julius.brain.dispatcher import OWNERS, Dispatcher
from julius.brain.intents import LiveIntentRouter


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.registry = dict(tools.REGISTRY)
        self.registry["get_weather"] = lambda **kw: "Sunny."
        self.d = Dispatcher(self.registry, tools.SCHEMAS)

    def test_every_schema_has_exact_owner(self):
        self.assertEqual(set(self.d.schemas), set(OWNERS))

    def test_success_has_status_owner_and_bounded_metadata_history(self):
        for _ in range(70):
            self.d.begin_turn()
            self.d.dispatch("get_weather", {"location": "Tokyo"})
        self.assertEqual(len(self.d.history), 64)
        self.assertEqual(self.d.last_result["agent"], "weather")
        self.assertTrue(self.d.last_result["ok"])
        self.assertNotIn("message", self.d.history[0])
        self.assertNotIn("args", self.d.history[0])

    def test_invalid_requests_never_call_worker(self):
        self.registry["get_weather"] = Mock()
        for name, args in [
            ("get_weather", []),
            ("get_weather", {"unknown": 1}),
            ("get_weather", {"location": 7}),
            ("execute_plan", {}),
            ("get_forecast", {"days": True}),
            ("get_forecast", {"days": 17}),
            ("get_scores", {"league": "nba", "day": "1999"}),
            ("get_quote", {"symbol": "AAPL/../../"}),
            ("read_file", {}),
            ("get_weather", {"location": ""}),
        ]:
            self.assertFalse(self.d.dispatch(name, args)["ok"])
        self.registry["get_weather"].assert_not_called()

    def test_network_timeout_and_unavailable_are_failures(self):
        import requests

        self.registry["get_weather"] = Mock(
            side_effect=requests.exceptions.Timeout("provider timed out")
        )
        self.assertEqual(self.d.dispatch("get_weather")["code"], "timeout")
        self.registry["get_weather"] = lambda: "Couldn't find that city."
        self.assertFalse(self.d.dispatch("get_weather")["ok"])

    def test_busy_rejects_without_starting_second_job_and_recovers(self):
        entered, release = threading.Event(), threading.Event()

        def slow():
            entered.set()
            release.wait(2)
            return "done"

        self.registry["list_planner_items"] = (
            slow  # planning is a serialized utility; weather/time/calculator lookups run in parallel
        )
        thread = threading.Thread(target=lambda: self.d.dispatch("list_planner_items"))
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            self.assertEqual(self.d.dispatch("read_document", {"path": "x.pdf"})["code"], "busy")
            self.assertEqual(
                self.d.dispatch("calculate", {"expression": "2+2"})["message"], "2+2 = 4"
            )
        finally:
            release.set()
            thread.join(2)
        self.assertEqual(self.d.dispatch("calculate", {"expression": "2+2"})["message"], "2+2 = 4")

    def test_file_failures_preserve_structured_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Documents").mkdir()
            self.d.file_agent = FileAgent(root, root / "state")
            r = self.d.dispatch("read_file", {"path": "missing.txt"})
            self.assertFalse(r["ok"])
            self.assertEqual(r["agent"], "files")
            session = FileSession(self.d.file_agent)
            self.assertIsNone(self.d.human_file(session, "Tell me a joke", 1))

    def test_calculator_rejects_executable_and_expensive_input(self):
        for expr in [
            '__import__("os").system("touch bad")',
            "().__class__",
            "[x for x in range(100)]",
            "2**10000000",
            "9**9**9",
            "True+1",
            "sqrt(-1)",
            "1/0",
            "1e100*1e100",
            "abs(x)",
            "solve(x**2-4,x)",
        ]:
            with self.subTest(expr=expr):
                self.assertFalse(self.d.dispatch("calculate", {"expression": expr})["ok"])
        self.assertAlmostEqual(evaluate("sqrt(9)+abs(-4)+2**3"), 15)

    def test_explicit_news_sports_quotes_route_without_model(self):
        r = LiveIntentRouter()
        self.assertEqual(r.resolve("latest news about space"), ("get_news", {"query": "space"}))
        self.assertEqual(
            r.resolve("NBA scores yesterday"), ("get_scores", {"league": "nba", "day": "yesterday"})
        )
        self.assertEqual(r.resolve("price of AAPL"), ("get_quote", {"symbol": "AAPL"}))
        self.assertIsNone(r.resolve("explain what stock prices mean"))

    def test_per_turn_budget_stops_tool_loops(self):
        self.d.begin_turn()
        for _ in range(8):
            self.assertTrue(self.d.dispatch("get_weather")["ok"])
        self.assertFalse(self.d.dispatch("get_weather")["ok"])
        self.d.begin_turn()
        self.assertTrue(self.d.dispatch("get_weather")["ok"])

    def test_actual_candidate_direct_routes_use_dispatcher(self):
        from tests.harness import TurnHarness

        h = TurnHarness()
        with (
            patch.object(tools, "_dispatcher", self.d),
            patch.dict(
                self.registry,
                {
                    "get_news": lambda **kw: {
                        "ok": True,
                        "code": "completed",
                        "message": "Example headline\nhttps://example.com",
                        "data": {},
                    },
                    "get_scores": lambda **kw: "FINAL: Home beat Away 5-3",
                },
            ),
        ):
            for text, owner in [
                ("17 times 4839", "calculator"),
                ("NBA scores yesterday", "sports"),
                ("news about space", "news"),
            ]:
                self.d.begin_turn()
                spoken = []
                reply = h.ask(text, "", speak_fn=spoken.append)
                self.assertEqual(self.d.last_result["agent"], owner)
                self.assertEqual(spoken, [reply])
                self.assertNotIn("https://", reply)

    def test_model_selected_tool_failure_cannot_be_rewritten_as_success(self):
        from types import SimpleNamespace as S

        from tests.harness import TurnHarness

        h = TurnHarness()
        tool_call = S(function=S(name="get_weather", arguments={"location": "Tokyo"}))
        model = Mock(return_value=S(message=S(tool_calls=[tool_call], content="")))
        h.llm_chat = model
        self.registry["get_weather"] = Mock(side_effect=TimeoutError("offline"))
        with patch.object(tools, "_dispatcher", self.d):
            spoken = []
            reply = h.ask(
                "Look up Tokyo current temperature", "", speak_fn=spoken.append
            )
        self.assertIn("failed", reply)
        self.assertEqual(spoken, [reply])
        self.assertEqual(model.call_count, 1)

    def test_inference_hook_runs_before_model_but_not_direct_tools(self):
        from types import SimpleNamespace as S

        from tests.harness import TurnHarness

        h = TurnHarness()
        order = []

        def model(**kwargs):
            order.append("model")
            return iter([S(message=S(content="Hello."))])

        h.llm_chat = model
        hook = lambda: order.append("voice_released")
        with patch.object(tools, "_dispatcher", self.d):
            h.ask("Say hello", "", speak_fn=lambda _: None, before_inference=hook)
            self.assertEqual(order, ["voice_released", "model"])
            order.clear()
            h.ask("2 plus 2", "", speak_fn=lambda _: None, before_inference=hook)
            self.assertEqual(order, [])

    def test_file_preview_status_and_unrelated_followup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Documents").mkdir()
            (root / "Documents" / "Archive").mkdir()
            path = root / "Documents" / "note.txt"
            path.write_text("note")
            session = FileSession(FileAgent(root, root / "state"))
            self.d.human_file(session, 'move "note.txt" to Archive', 1)
            self.assertEqual(self.d.last_result["status"], "needs_confirmation")
            self.assertTrue(path.exists())
            self.d.human_file(session, "NBA scores", 2)
            self.d.human_file(session, "yes", 3)
            self.assertTrue(path.exists())

    def test_ties_are_not_wins(self):
        competitors = [
            {"homeAway": "home", "team": {"displayName": "Home"}, "score": "5"},
            {"homeAway": "away", "team": {"displayName": "Away"}, "score": "5"},
        ]
        event = {
            "competitions": [{"competitors": competitors}],
            "status": {"type": {"completed": False, "state": "in"}},
        }
        with patch.object(
            tools.requests, "get", return_value=Mock(json=lambda: {"events": [event]})
        ):
            out = tools.get_scores("nba")
        self.assertIn("tied 5-5", out)
        self.assertNotIn("leads", out)

    def test_quote_keeps_actual_currency_and_timestamp(self):
        response = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 100,
                            "regularMarketTime": 1700000000,
                            "currency": "GBP",
                            "previousClose": 90,
                        }
                    }
                ]
            }
        }
        with patch.object(tools.requests, "get", return_value=Mock(json=lambda: response)):
            out = tools.get_quote("ABC")
        self.assertIn("GBP", out)
        self.assertIn("2023-11-14", out)
        self.assertIn("may be delayed", out)
        self.assertNotIn("today", out)

    def test_news_keeps_source_date_and_url(self):
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.news.return_value = [
            {
                "title": "Example",
                "source": "Publisher",
                "date": "2026-10-01",
                "url": "https://example.com/story",
                "body": "Snippet",
            }
        ]
        with patch("ddgs.DDGS", return_value=client):
            out = self.d.dispatch("get_news", {"query": "space"})
        self.assertTrue(out["ok"])
        self.assertIn("https://example.com/story", out["message"])
        self.assertEqual(out["data"]["items"][0]["source"], "Publisher")


if __name__ == "__main__":
    unittest.main()
