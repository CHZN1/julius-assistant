"""Typed messages from the orb: shown at once, stop the current answer, a short hold to edit or cancel, corrections replace the waiting one, repeats are dropped."""

import time
import unittest
from unittest import mock

import julius.ui_bridge.events as ev
from julius.runtime import inputs
from tests.harness import make_session


def session():
    s = make_session()
    hub = ev.EventHub()
    sent = []
    hub._send = lambda m: sent.append(m)
    s.events_hub = hub
    return s, sent


class TypedBarge(unittest.TestCase):
    def test_it_shows_in_the_transcript_at_once_as_pending(self):
        s, sent = session()
        out = inputs.ui_typed(s, "make me an nes sprite")
        line = sent[-1]
        self.assertEqual(
            (line["type"], line["who"], line["text"], line["pending"], line["id"]),
            ("transcript", "you", "make me an nes sprite", True, out["id"]),
        )

    def test_it_stops_the_answer_that_is_running(self):
        s, _ = session()
        with mock.patch("julius.runtime.controls.stop_now") as stop:
            inputs.ui_typed(s, "stop and do this instead")
        stop.assert_called_once_with(s, "typed")

    def test_a_correction_replaces_the_waiting_message_and_repeats_are_dropped(self):
        """The live test: "new", "nes", "nes", "nes" typed within seconds became four turns, each waiting up to 28 s."""
        s, sent = session()
        a = inputs.ui_typed(s, "New")
        b = inputs.ui_typed(s, "Nes")
        c = inputs.ui_typed(s, "nes")
        d = inputs.ui_typed(s, "nes.")
        self.assertEqual(len(s.typed), 1)
        self.assertEqual(str(s.typed[0]), "Nes")
        self.assertEqual(
            {a["id"], b["id"], c["id"], d["id"]}, {a["id"]}
        )  # one message, one line in the transcript
        self.assertEqual((b.get("merged"), c.get("merged")), ("replaced", "repeat"))
        lines = [m for m in sent if m.get("id") == a["id"]]
        self.assertEqual(lines[-1]["text"], "Nes")
        self.assertEqual(len([x for x in s.events_hub.transcript if x.get("id") == a["id"]]), 1)

    def test_the_same_text_right_after_it_was_picked_up_is_a_repeat(self):
        s, _ = session()
        inputs.ui_typed(s, "nes")
        s.current_typed, s.current_typed_at = s.typed.popleft(), time.monotonic()
        self.assertFalse(inputs.ui_typed(s, "nes")["queued"])
        self.assertEqual(len(s.typed), 0)
        self.assertTrue(inputs.ui_typed(s, "pico8 instead")["queued"])

    def test_edit_and_cancel_before_it_is_picked_up(self):
        s, sent = session()
        tid = inputs.ui_typed(s, "make me a sprit")["id"]
        self.assertTrue(inputs.ui_typed_change(s, tid, "make me a sprite")["ok"])
        self.assertEqual(str(s.typed[0]), "make me a sprite")
        self.assertEqual((sent[-1]["text"], sent[-1]["pending"]), ("make me a sprite", True))
        self.assertTrue(inputs.ui_typed_change(s, tid, None)["ok"])
        self.assertEqual(len(s.typed), 0)
        self.assertTrue(sent[-1]["cancelled"])
        self.assertFalse(
            any(x.get("id") == tid for x in s.events_hub.transcript)
        )  # gone from the history a reconnect sees
        self.assertFalse(inputs.ui_typed_change(s, tid, "too late")["ok"])

    def test_the_loop_holds_a_typed_message_briefly_then_runs_it(self):
        from julius.runtime import listen

        s, _ = session()
        inputs.ui_typed(s, "hello")
        loop = listen.Listener.__new__(listen.Listener)
        loop.s = s
        with (
            mock.patch.object(listen.Listener, "handle_typed") as h,
            mock.patch.object(listen.Listener, "expire_wake"),
            mock.patch.object(listen.Listener, "poll_reminders"),
        ):
            loop.step()
            h.assert_not_called()  # still inside the hold: it can be edited or cancelled
            s.typed[0].queued_at -= inputs.TYPED_HOLD_S + 0.1
            loop.step()
            h.assert_called_once()


if __name__ == "__main__":
    unittest.main()
