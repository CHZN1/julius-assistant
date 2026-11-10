from julius.paths import REPO_ROOT
import unittest
from julius.audio.lowconf import LowConfidenceGate, is_shaky, is_risky

SURE = {"segment_log_probabilities": [-0.2], "no_speech_probability": 0.01}
SHAKY = {"segment_log_probabilities": [-1.4], "no_speech_probability": 0.05}


class LowConfTests(unittest.TestCase):
    def test_shaky(self):
        self.assertTrue(is_shaky(SHAKY)); self.assertFalse(is_shaky(SURE)); self.assertFalse(is_shaky(None))
        self.assertTrue(is_shaky({"no_speech_probability": 0.5}))
        self.assertFalse(is_shaky({"rejected": "recognition_failure"}))

    def test_risky(self):
        for t in ("trash the old reports", "move the pdfs to Documents", "forget that", "merge it", "cancel the reminder"): self.assertTrue(is_risky(t), t)
        for t in ("what's the weather", "tell me a story", "how long is a mile"): self.assertFalse(is_risky(t), t)

    def test_confident_or_harmless_runs_straight_through(self):
        g = LowConfidenceGate()
        self.assertEqual(g.step("trash the old reports", SURE)[0], "run")
        self.assertEqual(g.step("what's the weather", SHAKY)[0], "run")
        self.assertEqual(g.step("trash the old reports", SHAKY, voice=False)[0], "run", "typed text is exact")

    def test_shaky_risky_asks_then_yes_runs_original(self):
        g = LowConfidenceGate(); a, t, ask = g.step("trash the old reports", SHAKY)
        self.assertEqual(a, "ask"); self.assertIn("Did you say: trash the old reports?", ask)
        a, t, ask = g.step("yeah", SURE)
        self.assertEqual((a, t), ("run", "trash the old reports"))
        self.assertEqual(g.step("hello", SURE)[0], "run", "no leftover pending state")

    def test_no_drops(self):
        g = LowConfidenceGate(); g.step("delete everything in downloads", SHAKY)
        a, t, ask = g.step("no", SURE); self.assertEqual(a, "drop"); self.assertTrue(ask)

    def test_other_words_replace_the_question(self):
        g = LowConfidenceGate(); g.step("trash the old reports", SHAKY)
        self.assertEqual(g.step("what time is it", SURE), ("run", "what time is it", None))
        self.assertEqual(g.step("yes", SURE)[0], "run"); self.assertIsNone(g.pending)

    def test_expires(self):
        now = [0.0]; g = LowConfidenceGate(clock=lambda: now[0], expires=10); g.step("trash the old reports", SHAKY); now[0] = 60
        self.assertEqual(g.step("yes", SURE), ("run", "yes", None))

    def test_candidate_wires_gate(self):
        src = open(REPO_ROOT / "julius/runtime.py").read()
        self.assertIn("julius.audio.lowconf", src); self.assertIn("_lowconf_gate.step", src)


if __name__ == "__main__":
    unittest.main()
