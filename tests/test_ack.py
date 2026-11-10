import time
import unittest

from julius.audio.ack import Acknowledger
from julius.paths import REPO_ROOT


class AckTests(unittest.TestCase):
    def test_slow_call_is_acknowledged_once(self):
        said = []
        a = Acknowledger(said.append, delay=0.05)
        with a.watch():
            time.sleep(0.2)
        with a.watch():
            time.sleep(0.2)
        self.assertEqual(said, ["On it."], "one acknowledgement per turn")

    def test_fast_call_is_silent(self):
        said = []
        a = Acknowledger(said.append, delay=0.2)
        with a.watch():
            time.sleep(0.01)
        time.sleep(0.3)
        self.assertEqual(said, [])

    def test_real_speech_cancels_it(self):
        said = []
        a = Acknowledger(said.append, delay=0.1)
        with a.watch():
            a.said_something()
            time.sleep(0.2)
        self.assertEqual(said, [])
        with a.watch():
            time.sleep(0.2)
        self.assertEqual(said, [], "stays quiet for the rest of the turn once he has spoken")

    def test_disabled_and_no_speaker(self):
        said = []
        with Acknowledger(said.append, delay=0.01, enabled=False).watch():
            time.sleep(0.05)
        with Acknowledger(None, delay=0.01).watch():
            time.sleep(0.05)
        self.assertEqual(said, [])

    def test_candidate_wraps_tool_calls(self):
        src = open(REPO_ROOT / "julius/runtime.py").read()
        self.assertIn("_ack.watch()", src)
        self.assertIn("from julius.audio import ack", src)


if __name__ == "__main__":
    unittest.main()
