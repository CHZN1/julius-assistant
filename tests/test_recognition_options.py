"""Recognition configuration retains auto mode and releases models on failure."""

import contextlib
import os
import unittest
from unittest.mock import Mock, patch

import numpy as np

from julius.runtime import asr
from tests.harness import make_session


class RecognitionOptionsTests(unittest.TestCase):
    def transcribe(self, s, recognizer, audio):
        with (
            patch.object(asr, "mlx_whisper", recognizer),
            patch.object(asr, "close_backend", Mock()),
            patch.object(asr, "recognition_resources", lambda release: contextlib.nullcontext()),
        ):
            return asr.transcribe(s, audio)

    def test_english_and_auto_options_reach_recognizer(self):
        recognizer = Mock()
        recognizer.transcribe.return_value = {
            "text": "overfitting",
            "language": "en",
            "segments": [],
        }
        s = make_session(asr_model="existing-model", asr_hint="")
        for language, expected in [("en", "en"), ("auto", None)]:
            with patch.dict(os.environ, {"JULIUS_ASR_LANGUAGE": language}):
                self.assertEqual(
                    self.transcribe(s, recognizer, np.ones(4096) * 0.02)[0], "overfitting"
                )
            kwargs = recognizer.transcribe.call_args.kwargs
            self.assertEqual(kwargs["language"], expected)
            self.assertNotIn("initial_prompt", kwargs)
            self.assertFalse(kwargs["condition_on_previous_text"])
        self.assertEqual(s.tts.suspend.call_count, 2)
        # The configured vocabulary hint reaches Whisper as initial_prompt.
        s.asr_hint = "Julius, AcmeCorp."
        self.transcribe(s, recognizer, np.ones(4096) * 0.02)
        hint = recognizer.transcribe.call_args.kwargs["initial_prompt"]
        self.assertTrue(hint.startswith("Julius, AcmeCorp, "), hint)  # the configured words first
        for word in ("sprite", "NES", "pico8", "Game Boy", "chibi", "palette", "goatee", "afro"):
            self.assertIn(word, hint)  # then the art vocabulary (and saved sprite names)


class SilentRecognitionTests(unittest.TestCase):
    def test_recognition_rejects_silence_before_model_load(self):
        recognizer = Mock()
        s = make_session()
        for audio in (np.zeros(16000), np.random.default_rng(1).normal(0, 0.001, 16000)):
            with patch.object(asr, "mlx_whisper", recognizer):
                self.assertEqual(asr.transcribe(s, audio), ("", "?", 1.0))
        recognizer.transcribe.assert_not_called()
        s.tts.suspend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
