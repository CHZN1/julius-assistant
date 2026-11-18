"""Unit tests for voice modes."""

import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from julius.audio.tts import SpeechEngine
from julius.audio.voice_identity import (
    REF_TEXT,
    configure_qwen_decoder,
    delivery_mode,
    numerical_failure,
    speech_chunks,
)
from julius.runtime import speech
from tests.harness import make_session


class VoiceModeTests(unittest.TestCase):
    def test_style_transitions(self):
        self.assertEqual(
            [
                delivery_mode(t)
                for t in [
                    "I am here.",
                    "Oh, fascinating!",
                    "We should check the results.",
                    "How did you do that?",
                    "Ha, I like that idea.",
                ]
            ],
            ["balanced", "lift", "balanced", "balanced", "lift"],
        )

    def test_numerical_only_retry(self):
        self.assertTrue(numerical_failure(RuntimeError("probability tensor contains inf, nan")))
        self.assertTrue(numerical_failure(FloatingPointError("bad audio")))
        self.assertFalse(numerical_failure(RuntimeError("MPS backend out of memory")))
        self.assertFalse(numerical_failure(RuntimeError("financial service unavailable")))

    def test_reference_matches_working_test(self):
        path = Path.home() / "Downloads/test_julius_clone_06b.py"
        if not path.exists():
            self.skipTest("Original local clone script unavailable")
        tree = ast.parse(path.read_text())
        ref = next(
            n.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "REF_TEXT" for t in n.targets)
        )
        self.assertEqual(REF_TEXT, ast.literal_eval(ref))


class GenerationSafetyTests(unittest.TestCase):
    def engine(self):
        e = SpeechEngine.__new__(SpeechEngine)
        e.backend = "qwen-clone"
        e.strategy = "two-prompt"
        e.dtype_name = "bfloat16"
        e.prompts = {"balanced": object(), "lift": object()}
        return e

    def test_long_text_preserved_and_style_held(self):
        text = "Overfitting happens when a model gets too closely tied to the training data, making it perform really well on that data but terribly on new, unseen data."
        chunks = list(speech_chunks(text))
        self.assertEqual(" ".join(chunks), text)
        self.assertTrue(all(len(c.split()) <= 12 for c in chunks))
        e = self.engine()
        e.delivery_mode = "balanced"
        e.model = Mock()
        e.model.generate_voice_clone.return_value = ([np.zeros(20, dtype="float32")], 24000)
        e._generate_qwen(text)
        self.assertEqual(
            [c.kwargs["text"] for c in e.model.generate_voice_clone.call_args_list], chunks
        )
        self.assertTrue(
            all(
                c.kwargs["voice_clone_prompt"] is e.prompts["balanced"]
                for c in e.model.generate_voice_clone.call_args_list
            )
        )

    def test_native_decoder_parameters(self):
        model = Mock()
        decoder = model.model.speech_tokenizer.model.decoder
        native = decoder.chunked_decode
        configure_qwen_decoder(model)
        decoder.chunked_decode("codes")
        native.assert_called_once_with("codes", chunk_size=96, left_context_size=25)

    def test_allocation_retry_preserves_words_and_prompt(self):
        e = self.engine()
        e.delivery_mode = "balanced"
        e.model = Mock()
        e.model.generate_voice_clone.side_effect = [
            RuntimeError("MPS backend out of memory"),
            ([np.zeros(20)], 24000),
            ([np.zeros(20)], 24000),
        ]
        text = "There is one thing we should check carefully."
        e._generate_qwen(text)
        calls = e.model.generate_voice_clone.call_args_list
        self.assertEqual(" ".join(c.kwargs["text"] for c in calls[1:]), text)
        self.assertTrue(all(c.kwargs["voice_clone_prompt"] is e.prompts["balanced"] for c in calls))
        self.assertEqual(e.allocation_retries, 1)

    def test_checkpoint_sampling_preserved(self):
        e = self.engine()
        e.delivery_mode = "lift"
        e.model = Mock()
        e.model.generate_voice_clone.return_value = ([np.zeros(20, dtype="float32")], 24000)
        e._generate_qwen("Oh, fascinating!")
        kwargs = e.model.generate_voice_clone.call_args.kwargs
        self.assertEqual(set(kwargs), {"text", "language", "voice_clone_prompt", "max_new_tokens"})
        self.assertIs(kwargs["voice_clone_prompt"], e.prompts["lift"])

    def test_allocation_failure_never_reloads_float32(self):
        e = self.engine()
        e._generate_qwen = Mock(side_effect=RuntimeError("MPS backend out of memory"))
        e._load_qwen = Mock()
        with self.assertRaises(RuntimeError):
            list(e.synthesize("Hello."))
        e._load_qwen.assert_not_called()

    def test_numerical_failure_retries_once(self):
        e = self.engine()
        e.model = Mock()
        e._generate_qwen = Mock(side_effect=[FloatingPointError("NaN"), (np.zeros(20), 24000)])
        e._load_qwen = Mock()
        self.assertEqual(len(list(e.synthesize("Oh, fascinating!"))), 1)
        self.assertEqual(e.dtype_name, "float32")
        self.assertEqual(e._load_qwen.call_count, 1)
        self.assertEqual(e._generate_qwen.call_count, 2)


class BackendReleaseTests(unittest.TestCase):
    def test_unload_does_not_reload_an_already_released_model(self):
        import julius.brain.backend as backend

        ollama = Mock()
        with (
            patch.dict("sys.modules", {"ollama": ollama}),
            patch.object(backend, "LLM_BACKEND", "ollama"),
            patch.object(backend, "_BACKEND_USED", True),
        ):
            backend.close_backend()
            backend.close_backend()
            self.assertEqual(ollama.generate.call_count, 1)
            self.assertFalse(backend._BACKEND_USED)


class ResponseDeliveryTests(unittest.TestCase):
    def test_reply_keeps_one_mode_and_restores_after_failure(self):
        s = make_session(tts=Mock(backend="qwen-clone", response_delivery_mode=None))
        seen = []

        def sentence(session, text):
            seen.append((text, session.tts.response_delivery_mode))

        with patch.object(speech, "speak_sentence", sentence):
            speech.speak(s, "Great, that worked! We should verify the result.")
        self.assertEqual([mode for _, mode in seen], ["lift", "lift"])
        self.assertIsNone(s.tts.response_delivery_mode)
        with (
            patch.object(speech, "speak_sentence", Mock(side_effect=RuntimeError("audio failed"))),
            self.assertRaises(RuntimeError),
        ):
            speech.speak(s, "Great!")
        self.assertIsNone(s.tts.response_delivery_mode)

    def test_question_does_not_imply_excitement(self):
        self.assertEqual(delivery_mode("Can you tell me what to explain?"), "balanced")
        self.assertEqual(delivery_mode("Great, that worked!"), "lift")


class WholeReplySpeechTests(unittest.TestCase):
    def test_mlx_reply_does_not_restart_playback_at_each_period(self):
        s = make_session(tts=Mock(backend="qwen-mlx", response_delivery_mode=None))
        text = "Great, that worked! We should still verify the result."
        with patch.object(speech, "speak_sentence", Mock()) as spoken:
            speech.speak(s, text)
        spoken.assert_called_once_with(s, text)
        self.assertIsNone(s.tts.response_delivery_mode)


if __name__ == "__main__":
    unittest.main()


class WrittenTranscriptTests(unittest.TestCase):
    """The transcript shows his words as written; only the voice gets the spoken form ("settings dot json", "three P M")."""

    def test_display_text_keeps_file_names_and_times(self):
        from julius.runtime.speech import display_text

        self.assertEqual(
            display_text("Open **settings.json** at `3 PM`."), "Open settings.json at 3 PM."
        )
        self.assertEqual(
            display_text("It costs $155.20 — about 10%."), "It costs $155.20 — about 10%."
        )
