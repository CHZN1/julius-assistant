"""Compound requests are not captured by single-tool shortcuts; simple ones still are."""

import unittest

from julius.brain.intents import LiveIntentRouter


class CompoundRoutingTests(unittest.TestCase):
    def resolve(self, text):
        return LiveIntentRouter().resolve(text)

    def test_trip_with_weather_and_time_goes_to_the_brain(self):
        self.assertIsNone(
            self.resolve(
                "Plan a three day trip to Tokyo for me, including the weather and what time it is there right now."
            )
        )

    def test_weather_and_time_in_one_request_goes_to_the_brain(self):
        self.assertIsNone(self.resolve("What's the weather in Tokyo and what time is it there?"))

    def test_several_places_in_one_request_go_to_the_brain(self):
        for text in [
            "What is the weather in Springfield and Tokyo?",
            "weather in Springfield and in Shelbyville",
            "what time is it in Paris and London?",
        ]:
            self.assertIsNone(self.resolve(text), text)

    def test_comparisons_and_organising_go_to_the_brain(self):
        for t in [
            "Compare the weather in Springfield and Tokyo",
            "Organize my downloads by type",
            "Research flights and then summarize",
        ]:
            self.assertIsNone(self.resolve(t), t)

    def test_single_requests_still_use_the_shortcut(self):
        self.assertEqual(
            self.resolve("What time is it in Tokyo?"), ("get_datetime", {"location": "Tokyo"})
        )
        self.assertEqual(
            self.resolve("What's the weather in Springfield?"),
            ("get_weather", {"location": "Springfield"}),
        )


if __name__ == "__main__":
    unittest.main()
