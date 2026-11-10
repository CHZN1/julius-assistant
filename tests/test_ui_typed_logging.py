"""Unit tests for the log events along the orb's typed-input path (received, queued, picked up, answered)."""

import unittest
from unittest.mock import Mock, patch

from julius.runtime import inputs
from julius.runtime.listen import Listener
from tests.harness import make_session

TEXT = "what time is it in tokyo"


def stages(log_event: Mock) -> list[str]:
    """The stage of every ui_typed event logged so far, in order."""
    return [c.kwargs["stage"] for c in log_event.call_args_list if c.args[0] == "ui_typed"]


class TypedPathLogging(unittest.TestCase):
    def setUp(self):
        self.s = make_session()
        patcher = patch("julius.log.event")
        self.event = patcher.start()
        self.addCleanup(patcher.stop)

    def take_and_answer(self, process: Mock):
        listener = Listener(self.s)
        with (
            patch("julius.runtime.listen.process", process),
            patch.object(Listener, "finish_exchange"),
            patch.object(Listener, "typed_reply_is_text", return_value=False),
        ):
            listener.handle_typed()

    def test_a_typed_line_is_queued_picked_up_and_answered_in_order(self):
        inputs.ui_typed(self.s, TEXT)
        self.take_and_answer(Mock())
        self.assertEqual(stages(self.event), ["queued", "picked_up", "answered"])

    def test_the_events_carry_timings_and_never_the_typed_text(self):
        inputs.ui_typed(self.s, TEXT)
        self.take_and_answer(Mock())
        by_stage = {c.kwargs["stage"]: c.kwargs for c in self.event.call_args_list}
        self.assertEqual(by_stage["queued"]["queue"], 1)
        self.assertGreaterEqual(by_stage["picked_up"]["waited"], 0)
        self.assertIn("seconds", by_stage["answered"])
        self.assertTrue(by_stage["answered"]["spoken"])
        self.assertNotIn(TEXT, repr(self.event.call_args_list))

    def test_a_failing_turn_is_logged_as_failed_and_still_raises(self):
        inputs.ui_typed(self.s, TEXT)
        with self.assertRaises(RuntimeError):
            self.take_and_answer(Mock(side_effect=RuntimeError("boom")))
        self.assertEqual(stages(self.event), ["queued", "picked_up", "failed"])
        failed = self.event.call_args_list[-1].kwargs
        self.assertEqual(failed["error"], "RuntimeError")

    def test_lines_typed_in_the_terminal_are_not_ui_events(self):
        self.s.typed.append(TEXT)
        self.take_and_answer(Mock())
        self.assertEqual(stages(self.event), [])


if __name__ == "__main__":
    unittest.main()
