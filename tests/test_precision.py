import os, unittest
from julius.agents import file_router
from julius.brain.intents import LiveIntentRouter


class ShortcutPrecisionTests(unittest.TestCase):
    def r(self, text):
        return LiveIntentRouter().resolve(text)

    def test_multi_intent_goes_to_brain(self):
        self.assertIsNone(self.r("Check the weather in Springfield, the price of Bitcoin, and the latest NHL scores"))
        self.assertIsNone(self.r("what's the weather and the time in Springfield"))
        self.assertIsNone(self.r("What's the weather in Springfield and Tokyo"))

    def test_single_location_only(self):
        self.assertEqual(self.r("weather in Springfield, Illinois"), ('get_weather', {'location': 'Springfield, Illinois'}))
        self.assertIsNone(self.r("weather in Shelbyville, Springfield"))

    def test_day_shortcut_limited_to_relative_days(self):
        self.assertEqual(self.r("what day is it tomorrow"), ('get_datetime', {'offset_days': 1}))
        self.assertEqual(self.r("what day is it"), ('get_datetime', {}))
        self.assertIsNone(self.r("What day of the week is March 3rd 2027?"))
        self.assertIsNone(self.r("What's the date next Friday"))

    def test_file_router_skips_planning_requests(self):
        root = os.path.expanduser('~')
        for t in ("Organize my downloads folder by file type.", "Sort my photos into folders by year.", "Tidy up my desktop.", "Clean up my downloads"):
            self.assertIsNone(file_router.route(t, root), t)
        self.assertEqual(file_router.route("Move all the PDFs from Downloads into a new folder named Papers", root)[0], 'move')

    def test_get_datetime_offset(self):
        from julius.brain import tools
        self.assertIn(',', tools.get_datetime(offset_days=1))


if __name__ == '__main__':
    unittest.main()


class EscalationGuidanceTests(unittest.TestCase):
    def test_prompt_separates_judgement_from_independent_lookups(self):
        from julius.brain import cloud
        text = cloud.PLANNING_PROMPT
        for needle in ("comparing options", "itinerary", "briefing", "renaming", "independent lookups"):
            self.assertIn(needle, text)

    def test_bulk_rename_is_not_a_shortcut(self):
        root = os.path.expanduser('~')
        self.assertIsNone(file_router.route("Rename every photo in my Pictures folder to include the year", root))
        self.assertEqual(file_router.route("rename report.pdf to q3.pdf", root)[0], 'rename')
