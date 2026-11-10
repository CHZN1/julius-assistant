"""Unit tests for listening."""

import unittest

from julius.audio.listening import has_wake, incomplete_fragment, listening_control, strip_wake


class ListeningTests(unittest.TestCase):
    def test_fragments_clarify_without_rejecting_short_answers(self):
        for text in ["You.", "Can you?", "I was..."]:
            self.assertTrue(incomplete_fragment(text))
        for text in [
            "Yes.",
            "No.",
            "Thanks.",
            "Why?",
            "Great, that worked!",
            "Can you explain this?",
        ]:
            self.assertFalse(incomplete_fragment(text))

    def test_wake_is_an_address_not_a_substring(self):
        for text in [
            "Julius, hello.",
            "Hey Julius, calculate two plus two.",
            "Hey, Julius!",
            "Juliet, hello.",
        ]:
            self.assertTrue(has_wake(text))
        for text in ["Tell me about Julius Caesar.", "Juliettes bakery", "a julius example"]:
            self.assertFalse(has_wake(text))

    def test_wake_only_has_no_question_text(self):
        self.assertEqual(strip_wake("Hey Julius!"), "")

    def test_control_only_accepts_exact_commands(self):
        self.assertEqual(listening_control("Julius, go to sleep."), "sleep")
        self.assertEqual(listening_control("Wake up!"), "wake")
        for text in ["Explain sleep.", "Stop listening to that song.", "Why do we go to sleep?"]:
            self.assertIsNone(listening_control(text))


if __name__ == "__main__":
    unittest.main()


class AudioActivityTests(unittest.TestCase):
    def test_silence_clicks_and_invalid_audio_are_rejected(self):
        import numpy as np

        from julius.audio.listening import has_voice_activity

        click = np.zeros(16000)
        click[4000:4300] = 0.1
        for audio in (np.zeros(16000), click, np.ones((16000, 2)), np.array([float("nan")])):
            self.assertFalse(has_voice_activity(audio))

    def test_short_sustained_answer_is_allowed(self):
        import numpy as np

        from julius.audio.listening import has_voice_activity

        answer = np.zeros(16000)
        answer[0:3200] = 0.03 * np.sin(np.arange(3200) * 0.1)
        self.assertTrue(has_voice_activity(answer))


class LeadingWakeWhileAwakeTests(unittest.TestCase):
    def test_leading_wake_word_is_dropped_so_follow_ups_still_route(self):
        from julius.audio.listening import strip_leading_wake
        from julius.brain.intents import LiveIntentRouter

        self.assertEqual(strip_leading_wake("Julius, what about tomorrow?"), "what about tomorrow?")
        self.assertEqual(
            strip_leading_wake("Hey Julius, what's 17 percent of 2480?"),
            "what's 17 percent of 2480?",
        )
        router = LiveIntentRouter()
        router.record("get_weather", {"location": "Springfield, Illinois"}, "out")
        self.assertEqual(
            router.resolve(strip_leading_wake("Julius, what about tomorrow?"))[0], "get_forecast"
        )

    def test_a_wake_word_glued_to_the_request_is_dropped_too(self):
        from julius.audio.listening import strip_leading_wake

        self.assertEqual(
            strip_leading_wake("JuliusTrashReport.txt in sandbox"), "TrashReport.txt in sandbox"
        )
        self.assertEqual(strip_leading_wake("Juliusville is a town"), "Juliusville is a town")

    def test_a_glued_wake_word_passes_the_wake_gate(self):
        """Live (wake_log 11-06 16:42:05 and 17:35:17): 'JuliusTrashReport.' was a real wake that the Whisper check rejected."""
        from julius.audio.listening import has_wake, strip_wake

        for t in (
            "JuliusTrashReport.",
            "JuliusTrashReport.TXT and Sandbox.",
            "Julius, trash report.txt",
            "Hey Julius.",
        ):
            self.assertTrue(has_wake(t), t)
        for t in (
            "Juliusville is a town",
            "Julia said she'd call Julian about the flight.",
            "Hey, it's Jules.",
            "Julie called",
        ):
            self.assertFalse(has_wake(t), t)
        self.assertEqual(
            strip_wake("JuliusTrashReport.TXT and Sandbox."), "TrashReport.TXT and Sandbox."
        )

    def test_bare_wake_word_and_ordinary_speech_are_unchanged(self):
        from julius.audio.listening import strip_leading_wake

        self.assertEqual(strip_leading_wake("Julius?"), "Julius?")
        self.assertEqual(strip_leading_wake("what about tomorrow?"), "what about tomorrow?")
        self.assertEqual(strip_leading_wake("Thanks, Julius. okay"), "Thanks, Julius. okay")
