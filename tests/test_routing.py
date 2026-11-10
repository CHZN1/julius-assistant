import unittest

from julius.brain.intents import LiveIntentRouter


class RoutingTests(unittest.TestCase):
    def setUp(self):
        from tests import profile_fixture

        profile_fixture.personal(self)

    def test_locations_and_followups(self):
        r = LiveIntentRouter()
        for text, expected in [
            ("What time is it in Tokyo?", ("get_datetime", {"location": "Tokyo"})),
            ("What about Egypt?", ("get_datetime", {"location": "Egypt"})),
            ("What's the weather today?", ("get_weather", {"location": "Springfield, Illinois"})),
            (
                "What about tomorrow?",
                ("get_forecast", {"location": "Springfield, Illinois", "days": 2}),
            ),
            (
                "What's the forecast for this week?",
                ("get_forecast", {"location": "Springfield, Illinois", "days": 7}),
            ),
        ]:
            actual = r.resolve(text)
            self.assertEqual(actual, expected)
            r.record(*actual, "real tool result")

    def test_temporal_phrases_are_not_locations(self):
        r = LiveIntentRouter()
        self.assertEqual(
            r.resolve("What's the forecast for this week in New York?"),
            ("get_forecast", {"location": "New York", "days": 7}),
        )
        self.assertEqual(
            r.resolve("Weather for Springfield for this week"),
            ("get_forecast", {"location": "Springfield", "days": 7}),
        )
        self.assertEqual(r.resolve("What's the date?"), ("get_datetime", {}))

    def test_no_weather_shortcut_for_explanations(self):
        self.assertIsNone(LiveIntentRouter().resolve("Explain how weather works"))

    def test_failure_spoken_without_invention(self):
        failure = "get_weather failed: Timeout"
        self.assertEqual(LiveIntentRouter().spoken_result("get_weather", failure), failure)

    def test_quote_speech_omits_unrequested_range(self):
        r = LiveIntentRouter()
        out = "Bitcoin: $80,000, up 1% today. 52-week range $50,000 to $100,000."
        self.assertEqual(r.spoken_result("get_quote", out), "Bitcoin: $80,000, up 1% today.")

    def test_context_resets(self):
        r = LiveIntentRouter()
        r.record("get_datetime", {"location": "Tokyo"}, "real result")
        r.resolve("Tell me a joke")
        self.assertIsNone(r.resolve("What about Egypt?"))


if __name__ == "__main__":
    unittest.main()
