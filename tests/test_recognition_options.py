from julius.paths import REPO_ROOT

"""Recognition configuration retains auto mode and releases models on failure."""
import ast
import contextlib
import os
import time
import unittest
from unittest.mock import Mock, patch


class RecognitionOptionsTests(unittest.TestCase):
    def test_english_and_auto_options_reach_recognizer(self):
        tree = ast.parse((REPO_ROOT / "julius/runtime.py").read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "transcribe")
        recognizer = Mock()
        recognizer.transcribe.return_value = {
            "text": "overfitting",
            "language": "en",
            "segments": [],
        }
        ns = {
            "os": os,
            "time": time,
            "tts": Mock(backend="qwen-mlx"),
            "close_backend": Mock(),
            "recognition_resources": lambda release: contextlib.nullcontext(),
            "mlx_whisper": recognizer,
            "ASR": "existing-model",
            "ASR_HINT": None,
        }
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "recognition", "exec"), ns)
        for language, expected in [("en", "en"), ("auto", None)]:
            with patch.dict(os.environ, {"JULIUS_ASR_LANGUAGE": language}):
                self.assertEqual(
                    ns["transcribe"](__import__("numpy").ones(4096) * 0.02)[0], "overfitting"
                )
            kwargs = recognizer.transcribe.call_args.kwargs
            self.assertEqual(kwargs["language"], expected)
            self.assertNotIn("initial_prompt", kwargs)
            self.assertFalse(kwargs["condition_on_previous_text"])
        self.assertEqual(ns["tts"].suspend.call_count, 2)
        ns["ASR_HINT"] = (
            "Julius, AcmeCorp."  # the configured vocabulary hint reaches Whisper as initial_prompt
        )
        ns["transcribe"](__import__("numpy").ones(4096) * 0.02)
        self.assertEqual(
            recognizer.transcribe.call_args.kwargs["initial_prompt"], "Julius, AcmeCorp."
        )


if __name__ == "__main__":
    unittest.main()


class SilentRecognitionTests(unittest.TestCase):
    def test_actual_recognition_rejects_silence_before_model_load(self):
        import numpy as np

        tree = ast.parse((REPO_ROOT / "julius/runtime.py").read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "transcribe")
        recognizer = Mock()
        engine = Mock(backend="qwen-mlx")
        ns = {
            "os": os,
            "time": time,
            "tts": engine,
            "mlx_whisper": recognizer,
            "close_backend": Mock(),
        }
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "recognition", "exec"), ns)
        for audio in (np.zeros(16000), np.random.default_rng(1).normal(0, 0.001, 16000)):
            self.assertEqual(ns["transcribe"](audio), ("", "?", 1.0))
        recognizer.transcribe.assert_not_called()
        engine.suspend.assert_not_called()
