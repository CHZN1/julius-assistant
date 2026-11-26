"""The wake greeting ("I am here.") shows as its own message, not glued onto the end of the previous reply (it carries the
previous reply's turn number, because only the user's lines start a turn)."""

import unittest
from types import SimpleNamespace

import julius.ui_bridge.events as ev
from julius.runtime import speech


class AloneLine(unittest.TestCase):
    def test_the_first_sentence_after_a_control_starts_a_new_message(self):
        hub = ev.EventHub()
        s = SimpleNamespace(events_hub=hub, turn=7, line_alone=False)
        speech._show(s, "It's 5:05 PM.")  # the previous reply, turn 7
        s.line_alone = True  # apply_control("wake"): "I am here."
        speech._show(s, "I am here.")
        speech._show(s, "What can I do?")  # a second sentence of the same line joins it
        lines = [x for x in hub.transcript if x["who"] == "julius"]
        self.assertEqual([x.get("alone", False) for x in lines], [False, True, False])
        self.assertEqual({x["turn"] for x in lines}, {7})
        self.assertFalse(s.line_alone)


if __name__ == "__main__":
    unittest.main()
