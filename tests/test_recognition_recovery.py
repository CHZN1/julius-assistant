import ast
import unittest
from unittest.mock import Mock, patch

from julius.paths import REPO_ROOT


class RecognitionRecoveryTests(unittest.TestCase):
    def wrapper(self, recognizer):
        tree = ast.parse((REPO_ROOT / "julius/runtime.py").read_text())
        fn = next(
            n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "transcribe_input"
        )
        ns = {"transcribe": recognizer}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "recognition_recovery", "exec"), ns)
        return ns

    def test_failed_turn_is_discarded_and_next_turn_recovers(self):
        recognizer = Mock(side_effect=[RuntimeError("failed decoder"), ("two plus two", "en", 0)])
        ns = self.wrapper(recognizer)
        with patch("builtins.print"):
            self.assertEqual(ns["transcribe_input"]([1]), ("", "?", 1.0))
        self.assertEqual(ns["_last_asr_confidence"]["rejected"], "recognition_failure")
        self.assertEqual(ns["transcribe_input"]([2]), ("two plus two", "en", 0))

    def test_interrupt_is_not_swallowed(self):
        ns = self.wrapper(Mock(side_effect=KeyboardInterrupt))
        with self.assertRaises(KeyboardInterrupt):
            ns["transcribe_input"]([1])

    def test_live_and_scripted_audio_use_recovery(self):
        tree = ast.parse((REPO_ROOT / "julius/runtime.py").read_text())
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "transcribe_input"
        ]
        self.assertEqual(
            len(calls), 3
        )  # scripted audio, the live utterance, and the cascade wake check


if __name__ == "__main__":
    unittest.main()
