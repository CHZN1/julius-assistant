"""Follow-up turns keep working: wake/courtesy prefixes, "and in Shelbyville?", tool carry-forward, calculator shortcut."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import julius.brain.tools as jt
from julius.audio.listening import normalize_request
from julius.brain.intents import LiveIntentRouter
from tests.harness import TurnHarness


def weather_router():
    r = LiveIntentRouter()
    r.record("get_weather", {"location": "Springfield, Illinois"}, "Springfield: 74F")
    return r


class NormalizeTests(unittest.TestCase):
    def test_wake_word_and_courtesy_openers_are_dropped(self):
        self.assertEqual(
            normalize_request("Thanks, Julius. What's 17% of 2480?"), "What's 17% of 2480?"
        )
        self.assertEqual(normalize_request("Julius, what about tomorrow?"), "what about tomorrow?")
        self.assertEqual(
            normalize_request("Okay, hey Julius, and in Shelbyville?"), "in Shelbyville?"
        )
        self.assertEqual(
            normalize_request("Okay, hey Julius, and in Shelbyville?", keep_and=True),
            "and in Shelbyville?",
        )

    def test_bare_courtesy_and_wake_words_are_left_alone(self):
        for text in ["Thanks.", "Julius?", "Okay, Julius", "what about tomorrow?"]:
            self.assertEqual(normalize_request(text), text)


class RouterFollowUpTests(unittest.TestCase):
    def test_what_about_tomorrow_after_weather_with_and_without_the_prefix(self):
        for text in [
            "what about tomorrow?",
            "Julius, what about tomorrow?",
            "Hey Julius, what about tomorrow",
            "Thanks, Julius. What about tomorrow?",
        ]:
            self.assertEqual(
                weather_router().resolve(text),
                ("get_forecast", {"location": "Springfield, Illinois", "days": 2}),
                text,
            )

    def test_bare_tomorrow_after_weather_routes_to_the_forecast(self):
        for text in [
            "and tomorrow",
            "tomorrow?",
            "Julius, and tomorrow",
            "how about tomorrow",
            "and tomorrow in Shelbyville",
        ]:
            want = (
                "get_forecast",
                {
                    "location": "Shelbyville"
                    if text.endswith("Shelbyville")
                    else "Springfield, Illinois",
                    "days": 2,
                },
            )
            self.assertEqual(weather_router().resolve(text), want, text)
        self.assertIsNone(LiveIntentRouter().resolve("and tomorrow"))

    def test_and_in_another_place_repeats_the_last_lookup_there(self):
        for text in [
            "and in Shelbyville?",
            "Julius, and in Shelbyville?",
            "what about in Shelbyville?",
            "and for Shelbyville",
        ]:
            self.assertEqual(
                weather_router().resolve(text), ("get_weather", {"location": "Shelbyville"}), text
            )
        r = LiveIntentRouter()
        r.record("get_datetime", {"location": "Tokyo"}, "out")
        self.assertEqual(r.resolve("and in Paris?"), ("get_datetime", {"location": "Paris"}))
        r = LiveIntentRouter()
        r.record("get_forecast", {"location": "Springfield", "days": 2}, "out")
        self.assertEqual(
            r.resolve("and in Shelbyville?"),
            ("get_forecast", {"location": "Shelbyville", "days": 2}),
        )

    def test_other_symbol_after_a_price(self):
        r = LiveIntentRouter()
        r.record("get_quote", {"symbol": "BTC"}, "out")
        self.assertEqual(r.resolve("and ETH?"), ("get_quote", {"symbol": "ETH"}))
        r = LiveIntentRouter()
        r.record("get_quote", {"symbol": "BTC"}, "out")
        self.assertEqual(r.resolve("what about bitcoin?"), ("get_quote", {"symbol": "BTC"}))
        r = LiveIntentRouter()
        r.record("get_quote", {"symbol": "BTC"}, "out")
        self.assertIsNone(r.resolve("what about tomorrow?"))

    def test_no_follow_up_without_a_previous_lookup(self):
        self.assertIsNone(LiveIntentRouter().resolve("and in Shelbyville?"))
        self.assertIsNone(LiveIntentRouter().resolve("what about tomorrow?"))

    def test_and_what_about_tomorrow_and_trailing_filler_words_still_route(self):
        for text in [
            "And what about tomorrow?",
            "Julius, and what about tomorrow?",
            "And what about tomorrow please?",
        ]:
            self.assertEqual(
                weather_router().resolve(text),
                ("get_forecast", {"location": "Springfield, Illinois", "days": 2}),
                text,
            )
        for text in [
            "And what's 17% of 2,480 again?",
            "What's 17% of 2480, please?",
            "Julius, what's 17% of 2480 for me?",
        ]:
            self.assertEqual(
                LiveIntentRouter().resolve(text), ("calculate", {"expression": "17/100*2480"}), text
            )
        self.assertEqual(
            LiveIntentRouter().resolve("And what's 17% of 2,480 again"),
            ("calculate", {"expression": "17/100*2480"}),
        )

    def test_a_leading_and_does_not_defeat_the_calculator_shortcut(self):
        self.assertEqual(
            LiveIntentRouter().resolve("And what's 17% of 2,480?"),
            ("calculate", {"expression": "17/100*2480"}),
        )
        self.assertEqual(
            LiveIntentRouter().resolve("And what is 6 times 7?"),
            ("calculate", {"expression": "6*7"}),
        )

    def test_calculator_shortcut_survives_courtesy_and_wake_prefix(self):
        for text in [
            "Thanks, Julius. What's 17% of 2480?",
            "Thanks, Julius. What's 17% of 2,480?",
            "Julius, what's 17 percent of 2480?",
        ]:
            self.assertEqual(
                LiveIntentRouter().resolve(text), ("calculate", {"expression": "17/100*2480"}), text
            )


class TurnTests(unittest.TestCase):
    def ask(self, h, text, spoken):
        return h.ask(text, "", speak_fn=spoken.append)

    def test_what_about_tomorrow_follow_up_runs_the_forecast_tool_through_the_real_turn_function(
        self,
    ):
        for second in ["what about tomorrow?", "Julius, what about tomorrow?"]:
            h = TurnHarness()
            spoken = []
            with patch.object(jt, "call", side_effect=lambda n, a: f"{n} ok") as call:
                self.ask(h, "What's the weather in Springfield?", spoken)
                self.ask(h, second, spoken)
            self.assertEqual(
                call.call_args_list[0].args, ("get_weather", {"location": "Springfield"}), second
            )
            self.assertEqual(
                call.call_args_list[1].args,
                ("get_forecast", {"location": "Springfield", "days": 2}),
                second,
            )

    def test_percent_question_with_courtesy_and_wake_word_hits_the_calculator_without_the_model(
        self,
    ):
        h = TurnHarness()
        spoken = []
        h.llm_chat = lambda **kw: self.fail("the model must not be called")
        self.ask(h, "Thanks, Julius. What's 17% of 2480?", spoken)
        self.assertEqual(spoken, ["That's 421.6."])

    def test_a_turn_that_used_a_tool_carries_tools_into_the_next_turn(self):
        def run(first):
            h = TurnHarness()
            calls = []
            tool_turns = [0]

            def llm(**kw):
                calls.append(kw)
                if kw.get("stream"):
                    return iter([SimpleNamespace(message=SimpleNamespace(content="Done. "))])
                tool_turns[0] += 1
                if tool_turns[0] == 1 and first:
                    return SimpleNamespace(
                        message=SimpleNamespace(
                            content="",
                            tool_calls=[
                                SimpleNamespace(
                                    function=SimpleNamespace(
                                        name="get_scores",
                                        arguments={"league": "mlb", "day": "yesterday"},
                                    )
                                )
                            ],
                        )
                    )
                return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None))

            h.llm_chat = llm
            spoken = []
            with patch.object(jt, "call", return_value="Giants lost 3-2"):
                jt.dispatcher().last_result = {"ok": True}
                if first:
                    self.ask(h, "Who won the Giants game yesterday?", spoken)
                before = len([c for c in calls if not c.get("stream")])
                self.ask(h, "And the Dodgers?", spoken)
            tool_turns_calls = [c for c in calls if not c.get("stream")]
            return len(tool_turns_calls) - before, bool(
                tool_turns_calls and tool_turns_calls[-1].get("tools") is not None
            )

        self.assertEqual(run(first=True), (1, True))  # follow-up gets a tool turn with tools
        self.assertEqual(
            run(first=False)[0], 0
        )  # without a previous tool, plain chat stays tool-free

    def test_carry_lasts_one_turn_unless_a_tool_is_used_again(self):
        h = TurnHarness()
        h.session.tool_carry_next = True
        spoken = []
        tool_calls = []

        def llm(**kw):
            if kw.get("stream"):
                return iter([SimpleNamespace(message=SimpleNamespace(content="Fine. "))])
            tool_calls.append(kw.get("tools") is not None)
            return SimpleNamespace(message=SimpleNamespace(content="", tool_calls=None))

        h.llm_chat = llm
        self.ask(h, "And the Dodgers?", spoken)
        self.ask(h, "And the Padres?", spoken)
        self.assertEqual(tool_calls, [True])  # second turn is plain chat again


if __name__ == "__main__":
    unittest.main()
