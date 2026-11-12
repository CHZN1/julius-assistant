"""Model calls per turn type: a lookup that needs the model's summary is exactly two calls; shortcuts and ready sentences need fewer."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import julius.brain.tools as jt
from tests.harness import TurnHarness


def tool_call(*calls):
    return SimpleNamespace(
        message=SimpleNamespace(
            content="",
            tool_calls=[
                SimpleNamespace(function=SimpleNamespace(name=n, arguments=a)) for n, a in calls
            ],
        )
    )


def as_parts(r):
    """A scripted non-streaming reply as the parts a streaming cloud brain yields: its text, then any tool calls."""
    parts = (
        [SimpleNamespace(message=SimpleNamespace(content=r.message.content or "", tool_calls=None))]
        if r.message.content
        else []
    )
    if r.message.tool_calls:
        parts.append(
            SimpleNamespace(message=SimpleNamespace(content="", tool_calls=r.message.tool_calls))
        )
    return iter(parts)


def text(t):
    return SimpleNamespace(message=SimpleNamespace(content=t, tool_calls=None))


def turn(prompt, responses, cloud=True, out="tool output", carry=False, many=False):
    """Run ask_streaming with scripted non-streaming responses; returns (model calls, spoken, result)."""
    h = TurnHarness()
    h.session.cloud_brain = object() if cloud else None
    h.session.tool_carry_next = carry
    calls = []
    spoken = []
    queue = list(responses)

    def llm(**kw):
        calls.append(bool(kw.get("stream")))
        if kw.get("stream") and "tools" not in kw:
            return iter([SimpleNamespace(message=SimpleNamespace(content="Spoken summary."))])
        r = queue.pop(0) if queue else text("")
        return as_parts(r) if kw.get("stream") else r

    h.llm_chat = llm
    many_patch = (
        patch.object(jt, "call_many", side_effect=lambda cs: [(out, True) for _ in cs])
        if many
        else patch.object(jt, "call_many", wraps=jt.call_many)
    )
    with patch.object(jt, "call", return_value=out), many_patch:
        jt.dispatcher().last_result = {"ok": True}
        result = h.ask(prompt, "", speak_fn=spoken.append)
    return calls, spoken, result


WEATHER = "Springfield: 76°F, clear, feels like 76. High 77, low 50."


class CallCountTests(unittest.TestCase):
    def test_router_shortcut_makes_no_model_call(self):
        calls, spoken, _ = turn("What's 17% of 2480?", [])
        self.assertEqual(calls, [])
        calls, _, _ = turn("What's the weather in Springfield?", [], out=WEATHER)
        self.assertEqual(calls, [])

    def test_plain_chat_is_one_streamed_call_locally_and_one_tool_enabled_call_in_the_cloud(self):
        calls, spoken, _ = turn("How are you doing today?", [], cloud=False)
        self.assertEqual(calls, [True])
        self.assertEqual(spoken, ["Spoken summary."])
        calls, spoken, _ = turn("How are you doing today?", [text("Doing well, thanks.")])
        self.assertEqual(calls, [True])
        self.assertEqual(spoken, ["Doing well, thanks."])

    def test_cloud_offers_the_price_tool_on_a_price_question_with_no_price_keywords(self):
        for phrase in (
            "What's their eath at right now?",
            "how's it looking over there",
            "and what about that one",
        ):
            h = TurnHarness()
            h.session.cloud_brain = object()
            seen = []
            h.llm_chat = lambda **kw: (
                seen.append(kw.get("tools")) or as_parts(text("Ethereum is around 2,300 dollars."))
            )
            h.ask(phrase, "", speak_fn=lambda t: None)
            names = {t["function"]["name"] for t in seen[0]}
            self.assertIn("get_quote", names, phrase)

    def test_simple_lookup_through_the_model_is_one_call_and_a_ready_sentence(self):
        for name, args, out in [
            ("get_weather", {"location": "Springfield"}, WEATHER),
            (
                "get_quote",
                {"symbol": "ETH"},
                "Ethereum USD (ETH-USD): 2,309.82 USD, down 17.05 (0.73%) versus previous close.",
            ),
            ("calculate", {"expression": "2+2"}, "2+2 = 4"),
        ]:
            calls, spoken, result = turn(
                "Is it cold outside in Springfield?", [tool_call((name, args))], out=out
            )
            self.assertEqual(calls, [True], name)
            self.assertEqual(spoken, [result])

    def test_an_identical_tool_call_repeated_in_one_turn_runs_once_and_stays_one_model_call(self):
        eth = "Ethereum USD (ETH-USD): 2,308.57 USD, down 18.35 (0.79%) versus previous close."
        h = TurnHarness()
        h.session.cloud_brain = object()
        calls = []
        spoken = []
        h.llm_chat = lambda **kw: (
            calls.append(bool(kw.get("stream")))
            or as_parts(
                tool_call(("get_quote", {"symbol": "ETH"}), ("get_quote", {"symbol": "ETH"}))
            )
        )
        with patch.object(jt, "call", return_value=eth) as call:
            jt.dispatcher().last_result = {"ok": True}
            result = h.ask("And what's the current price of ETH?", "", speak_fn=spoken.append)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(calls, [True])
        self.assertEqual(spoken, [result])
        self.assertIn("about 2,309 dollars", result)

    def test_a_lookup_that_needs_the_model_summary_is_exactly_two_calls(self):
        calls, spoken, result = turn(
            "List the files in my Downloads folder",
            [tool_call(("list_files", {"directory": "Downloads"})), text("Spoken summary.")],
            out="a.txt\nb.txt",
        )
        self.assertEqual(calls, [True, True])
        self.assertEqual(result, "Spoken summary.")

    def test_several_lookups_in_one_turn_are_still_exactly_two_calls(self):
        calls, _, _ = turn(
            "What is the weather in Springfield and in Tokyo?",
            [
                tool_call(
                    ("get_weather", {"location": "Springfield"}),
                    ("get_weather", {"location": "Tokyo"}),
                ),
                text("Both are fine."),
            ],
            out=WEATHER,
            many=True,
        )
        self.assertEqual(calls, [True, True])

    def test_a_tool_turn_where_no_tool_is_needed_is_one_call_in_cloud_mode_and_two_locally(self):
        calls, spoken, _ = turn(
            "explain overfitting simply",
            [text("Overfitting is memorizing the training data. It fails on new data.")],
            cloud=True,
            carry=True,
        )
        self.assertEqual(calls, [True])
        self.assertEqual(len(spoken), 2)
        calls, _, _ = turn(
            "explain overfitting simply", [text("tool-phase text")], cloud=False, carry=True
        )
        self.assertEqual(calls, [False, True])

    def test_a_tool_turn_never_exceeds_three_rounds_and_a_final_answer(self):
        endless = [tool_call(("list_files", {"directory": "x"})) for _ in range(5)]
        calls, _, _ = turn("List the files in my Downloads folder", endless, out="a\nb")
        self.assertLessEqual(len(calls), 4)

    def test_a_cloud_plain_chat_turn_starts_speaking_before_the_reply_has_finished(self):
        h = TurnHarness()
        h.session.cloud_brain = object()
        events = []

        def llm(**kw):
            assert kw.get("stream") and kw.get("tools")

            def gen():
                yield SimpleNamespace(
                    message=SimpleNamespace(content="Overfitting is memorizing. ", tool_calls=None)
                )
                events.append("first chunk sent")
                yield SimpleNamespace(
                    message=SimpleNamespace(content="It fails on new data.", tool_calls=None)
                )
                events.append("reply finished")

            return gen()

        h.llm_chat = llm
        h.ask("explain overfitting simply", "", speak_fn=lambda t: events.append("spoke: " + t))
        self.assertLess(
            events.index("spoke: Overfitting is memorizing."), events.index("reply finished")
        )

    def test_a_tool_call_arriving_after_streamed_text_is_run_and_answered(self):
        h = TurnHarness()
        h.session.cloud_brain = object()
        spoken = []
        h.llm_chat = lambda **kw: iter(
            [
                SimpleNamespace(message=SimpleNamespace(content="Sure thing. ", tool_calls=None)),
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="",
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="get_weather", arguments={"location": "Springfield"}
                                )
                            )
                        ],
                    )
                ),
            ]
        )
        with patch.object(jt, "call", return_value=WEATHER):
            jt.dispatcher().last_result = {"ok": True}
            h.ask("Is it cold outside in Springfield?", "", speak_fn=spoken.append)
        self.assertEqual(
            spoken,
            [
                "Sure thing.",
                "It's 76 degrees and clear in Springfield, with a high of 77 and a low of 50.",
            ],
        )


if __name__ == "__main__":
    unittest.main()


class ChainTests(unittest.TestCase):
    """Up to [brain] max_tool_rounds (3) tool rounds per turn: 'look up X, then chart it'."""

    def run_chain(self, prompt, script):
        h = TurnHarness()
        h.session.cloud_brain = object()
        names, queue, spoken = [], list(script), []

        def llm(**kw):
            r = queue.pop(0) if queue else text("")
            return as_parts(r) if kw.get("stream") else r

        h.llm_chat = llm

        def call(name, args):
            names.append(name)
            return "chart shown" if name == "render_chart" else "AAPL closes: 1, 2, 3"

        with patch.object(jt, "call", side_effect=call):
            jt.dispatcher().last_result = {"ok": True}
            result = h.ask(prompt, "", speak_fn=spoken.append)
        return names, spoken, result, queue

    def test_price_tool_then_render_chart(self):
        names, spoken, _, left = self.run_chain(
            "chart Apple's closing price over the last 5 days",
            [
                tool_call(("get_quote", {"symbol": "Apple", "days": 5})),
                tool_call(
                    (
                        "render_chart",
                        {"kind": "line", "title": "AAPL", "labels": ["a"], "series": []},
                    )
                ),
                text("It shows Apple's last five closes."),
            ],
        )
        self.assertEqual(names, ["get_quote", "render_chart"])
        self.assertEqual(spoken, ["It shows Apple's last five closes."])
        self.assertEqual(left, [])

    def test_rounds_are_capped_at_three(self):
        script = [tool_call(("get_quote", {"symbol": "Apple"})) for _ in range(5)] + [text("done")]
        names, _, _, left = self.run_chain(
            "what is Apple worth, then Tesla, then Ford, then GM", script
        )
        self.assertEqual(len(names), 3)

    def test_turn_budget_stops_further_rounds(self):
        with patch("julius.runtime.brain_turn.BrainTurn.turn_over_budget", return_value=True):
            names, _, _, _ = self.run_chain(
                "chart Apple's closing price",
                [tool_call(("get_quote", {"symbol": "Apple"})) for _ in range(3)] + [text("ok")],
            )
        self.assertEqual(len(names), 1)

    def test_research_phrases_force_the_research_agent(self):
        for t in (
            "Research the history of the bridge",
            "look up how tall the Burj Khalifa is",
            "Can you find sources on fasting",
            "search the web for hiking trails",
            "Please do some research on heat pumps",
        ):
            self.assertTrue(jt.research_required(t), t)
        for t in ("What's the weather", "plan a trip", "Tell me about research funding"):
            self.assertFalse(jt.research_required(t), t)
