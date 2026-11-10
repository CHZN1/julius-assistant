"""Going to sleep releases model weights; the messages say exactly what was released."""

import contextlib
import unittest
from unittest.mock import Mock, patch

from julius.runtime import handlers
from tests.harness import make_session


class IdleReleaseTests(unittest.TestCase):
    def release(self, backend="ollama", environment=None, suspend_error=None):
        """Run release_idle_models with recorded collaborators: (result, events, last message)."""
        events = []
        s = make_session()
        s.tts.suspend.side_effect = suspend_error or (lambda: events.append("voice"))

        @contextlib.contextmanager
        def recognition(release=True):
            events.append("recognition" if release else "recognition-kept")
            yield

        printed = Mock()
        with (
            patch.object(handlers, "close_backend", lambda: events.append("brain")),
            patch.object(handlers, "recognition_resources", recognition),
            patch.object(handlers, "LLM_BACKEND", backend),
            patch.dict("os.environ", environment or {}, clear=False),
            patch("builtins.print", printed),
        ):
            result = handlers.release_idle_models(s)
        return result, events, printed.call_args.args[0]

    def test_sleep_releases_voice_brain_and_recognition(self):
        result, events, _ = self.release()
        self.assertTrue(result)
        self.assertEqual(events, ["voice", "brain", "recognition"])

    def test_failed_release_is_reported_without_loading_other_models(self):
        result, events, message = self.release(suspend_error=RuntimeError("failed unload"))
        self.assertFalse(result)
        self.assertFalse(events)
        self.assertIn("idle release failed", message)

    def test_message_says_all_weights_released_with_a_local_brain(self):
        _, _, message = self.release("ollama", {"JULIUS_KEEP_VOICE": "0"})
        self.assertIn("idle model weights released", message)

    def test_cloud_profile_message_only_claims_speech_recognition_was_released(self):
        _, _, message = self.release("cloud", {"JULIUS_KEEP_VOICE": "1", "JULIUS_ASR_RESIDENT": "0"})
        self.assertIn("speech recognition released", message)
        self.assertIn("voice stays loaded", message)
        self.assertNotIn("model weights released", message)

    def test_resident_whisper_stays_loaded_while_sleeping(self):
        _, events, message = self.release(
            "cloud", {"JULIUS_KEEP_VOICE": "1", "JULIUS_ASR_RESIDENT": "1"}
        )
        self.assertIn("recognition-kept", events)
        self.assertNotIn("speech recognition released", message)


if __name__ == "__main__":
    unittest.main()
