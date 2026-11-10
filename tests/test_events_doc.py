from julius.paths import REPO_ROOT

"""The feed document is the interface to the private UI repo: it must match the code."""
import re
import unittest

import julius.ui_bridge.events as ev

DOC = (REPO_ROOT / "docs" / "EVENT_FEED.md").read_text()


class FeedDocMatchesCode(unittest.TestCase):
    def test_every_state_is_documented_and_no_extra(self):
        listed = set(re.findall(r"`(\w+)`", DOC.split("States:")[1].split("\n\n")[0]))
        self.assertEqual(listed, set(ev.STATES))

    def test_message_types_are_documented(self):
        for t in ("hello", "state", "level", "transcript"):
            self.assertIn(f"| `{t}` |", DOC)

    def test_port_host_and_limits_match(self):
        self.assertIn("127.0.0.1:8765", DOC)
        self.assertEqual(ev.HOST, "127.0.0.1")
        self.assertEqual(ev.MAX_CLIENTS, 8)
        self.assertIn("at most 8 clients".lower(), DOC.lower())
        self.assertIn("300 messages", DOC)
        self.assertIn("64 kB", DOC)
        self.assertEqual(ev.LEVEL_GAIN, 4.0)
        self.assertIn("RMS x 4", DOC)

    def test_a_real_hello_has_the_documented_fields(self):
        h = ev.EventHub().hello()
        self.assertEqual(set(h), {"type", "state", "level", "transcript", "t"})
        hub = ev.EventHub()
        seen = []
        hub._send = seen.append
        hub.set_state("thinking")
        hub.say("you", "hi", 1)
        hub.set_level(0.1)
        self.assertEqual(set(seen[0]), {"type", "state", "prev", "t"})
        self.assertEqual(set(seen[1]), {"type", "who", "text", "turn", "t"})
        self.assertEqual(set(seen[2]), {"type", "level", "t"})


if __name__ == "__main__":
    unittest.main()
