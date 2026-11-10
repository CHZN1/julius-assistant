"""Exercise actual turn recovery paths without models or devices."""

import unittest
from unittest.mock import Mock, patch

from julius.audio.cancellation import TurnCancelled
from julius.runtime import speech
from tests.harness import ProcessHarness, make_session


class TurnRecoveryTests(unittest.TestCase):
    def test_cancelled_reasoning_unloads_without_speaking_error(self):
        h = ProcessHarness()
        h["turn.ask_streaming"].side_effect = TurnCancelled("stop")
        h.process("Explain that.")
        h["turn.close_backend"].assert_called_once()
        h["turn.speak"].assert_not_called()
        h["turn.speak_sentence"].assert_not_called()
        self.assertFalse(h.session.cancellation.busy.is_set())

        def reply(*args, **kwargs):
            kwargs["speak_fn"]("A response.")
            return "A response."

        h["turn.ask_streaming"].side_effect = reply
        h.process("Try the next question.")
        h["turn.speak"].assert_called_once_with(h.session, "A response.")

    def test_model_failure_unloads_before_recovery_speech(self):
        h = ProcessHarness()
        order = []
        h["turn.ask_streaming"].side_effect = RuntimeError("model failed")
        h["turn.close_backend"].side_effect = lambda: order.append("unload")
        h["turn.speak"].side_effect = lambda *_: order.append("speak")
        h.process("Explain that.")
        self.assertEqual(order, ["unload", "speak"])

    def test_incomplete_input_asks_without_reasoning(self):
        h = ProcessHarness()
        h.process("You.", "en")
        h["turn.ask_streaming"].assert_not_called()
        h["turn.handle_files"].assert_not_called()
        self.assertIn("say that again", h["turn.speak"].call_args.args[1])

    def test_voice_does_not_load_if_reasoning_release_fails(self):
        s = make_session()
        with (
            patch.object(speech, "close_backend", Mock(side_effect=RuntimeError("unload failed"))),
            patch.object(speech, "keep_brain_loaded", Mock(return_value=False)),
            self.assertRaisesRegex(RuntimeError, "unload failed"),
        ):
            speech.speak_sentence(s, "Hello.")
        s.tts.synthesize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
