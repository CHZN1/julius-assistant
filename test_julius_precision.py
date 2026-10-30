import os, unittest
import julius_router
from julius_intents import LiveIntentRouter


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
            self.assertIsNone(julius_router.route(t, root), t)
        self.assertEqual(julius_router.route("Move all the PDFs from Downloads into a new folder named Papers", root)[0], 'move')

    def test_get_datetime_offset(self):
        import julius_tools
        self.assertIn(',', julius_tools.get_datetime(offset_days=1))


if __name__ == '__main__':
    unittest.main()
