import unittest

from julius.audio.listening import CaptureClock
from julius.paths import REPO_ROOT


class CaptureClockTests(unittest.TestCase):
    def test_buffered_audio_does_not_become_a_capture_gap(self):
        clock = CaptureClock()
        # Feed an entire queued recording in a burst: capture spacing is unchanged.
        for frame in range(200):
            self.assertFalse(clock.discontinuous(10 + frame * 0.064))

    def test_sleep_gap_resets_once_and_next_frames_continue(self):
        clock = CaptureClock()
        self.assertFalse(clock.discontinuous(1))
        self.assertTrue(clock.discontinuous(91))
        self.assertFalse(clock.discontinuous(91.064))

    def test_clock_discontinuity_does_not_produce_negative_utterance_duration(self):
        clock = CaptureClock()
        clock.discontinuous(100)
        self.assertTrue(clock.discontinuous(99))
        self.assertFalse(clock.discontinuous(99.064))

    def test_natural_hesitation_is_not_a_capture_gap(self):
        clock = CaptureClock()
        # Silent frames still arrive while the user pauses; amplitude is irrelevant.
        for frame in range(40):
            self.assertFalse(clock.discontinuous(frame * 0.064))

    def test_invalid_configuration_and_timestamps(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                CaptureClock(value)
        clock = CaptureClock()
        with self.assertRaises(ValueError):
            clock.discontinuous(float("nan"))
        self.assertIsNone(clock.previous)


if __name__ == "__main__":
    unittest.main()


class RuntimeCaptureResetTests(unittest.TestCase):
    def test_actual_resume_branch_discards_partial_audio(self):
        import ast
        from collections import deque
        from unittest.mock import Mock

        tree = ast.parse((REPO_ROOT / "julius/runtime.py").read_text())
        branch = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Call)
            and isinstance(n.test.func, ast.Attribute)
            and n.test.func.attr == "discontinuous"
        )
        clock = CaptureClock()
        clock.discontinuous(1)
        scope = {
            "capture_clock": clock,
            "now": 91,
            "buf": ["old audio"],
            "recording": True,
            "sil_t": 1,
            "spk_t": 0.5,
            "voiced_run": 2,
            "longest_voiced_run": 3,
            "pre_roll": deque(["old preroll"]),
            "print": Mock(),
            "wake_engine": Mock(),
        }
        exec(compile(ast.Module(body=[branch], type_ignores=[]), "resume", "exec"), scope)
        scope["wake_engine"].reset.assert_called_once()  # a gap also restarts the wake stream
        self.assertFalse(scope["recording"])
        self.assertEqual(scope["buf"], [])
        self.assertFalse(scope["pre_roll"])
        self.assertIsNone(scope["sil_t"])
        self.assertIsNone(scope["spk_t"])
        self.assertEqual(scope["longest_voiced_run"], 0)
