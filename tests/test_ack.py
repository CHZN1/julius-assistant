import contextlib
import time
import unittest
from unittest.mock import Mock

from julius.audio.ack import Acknowledger


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

    def test_tool_calls_run_inside_the_acknowledger_watch(self):
        from types import SimpleNamespace
        from unittest.mock import patch

        import julius.brain.tools as jt
        from julius.runtime.brain_turn import BrainTurn
        from tests.harness import make_session

        turn = BrainTurn(make_session(), "weather", None, None, False, None)
        turn.ack = Mock()
        turn.ack.watch.return_value = contextlib.nullcontext()
        call = SimpleNamespace(function=SimpleNamespace(name="get_weather", arguments={}))
        jt.dispatcher().last_result = {"ok": True}
        with patch.object(jt, "call", return_value="Sunny."):
            turn.run_calls([call])
        turn.ack.watch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
