"""A failed recognition must not stop listening; every path goes through the recovering wrapper."""

import ast
import unittest
from unittest.mock import Mock, patch

from julius.paths import REPO_ROOT
from julius.runtime import asr
from tests.harness import make_session


class RecognitionRecoveryTests(unittest.TestCase):
    def test_failed_turn_is_discarded_and_next_turn_recovers(self):
        s = make_session()
        recognizer = Mock(side_effect=[RuntimeError("failed decoder"), ("two plus two", "en", 0)])
        with patch.object(asr, "transcribe", recognizer), patch("builtins.print"):
            self.assertEqual(asr.transcribe_input(s, [1]), ("", "?", 1.0))
            self.assertEqual(s.last_asr_confidence["rejected"], "recognition_failure")
            self.assertEqual(asr.transcribe_input(s, [2]), ("two plus two", "en", 0))

    def test_interrupt_is_not_swallowed(self):
        with (
            patch.object(asr, "transcribe", Mock(side_effect=KeyboardInterrupt)),
            self.assertRaises(KeyboardInterrupt),
        ):
            asr.transcribe_input(make_session(), [1])

    def test_live_and_scripted_audio_use_recovery(self):
        calls = 0
        raw_calls = 0
        for path in (REPO_ROOT / "julius/runtime").glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    calls += node.func.id == "transcribe_input"
                    raw_calls += node.func.id == "transcribe" and path.name != "asr.py"
        # scripted audio, the live utterance, and the cascade wake check
        self.assertEqual(calls, 3)
        self.assertEqual(raw_calls, 0)


if __name__ == "__main__":
    unittest.main()
