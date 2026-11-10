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
    def test_resume_branch_discards_partial_audio(self):
        from collections import deque
        from unittest.mock import Mock, patch

        import numpy as np

        from julius.runtime.listen import Listener
        from tests.harness import make_session

        clock = CaptureClock()
        clock.discontinuous(1)
        s = make_session(capture_clock=clock, wake_engine=Mock(), recording=True)
        s.pre_roll = deque(["old preroll"])
        listener = Listener(s)
        listener.buffer = ["old audio"]
        listener.silence_started, listener.speech_started = 1, 0.5
        listener.voiced_run, listener.longest_voiced_run = 2, 3
        s.chunks.append((np.zeros(1024, dtype=np.float32), 91))
        with patch("builtins.print"):
            listener.process_chunk(awake=True)
        s.wake_engine.reset.assert_called_once()  # a gap also restarts the wake stream
        self.assertFalse(s.recording)
        self.assertEqual(listener.buffer, [])
        self.assertEqual(len(s.pre_roll), 1)  # the old pre-roll is gone; only the new block is kept
        self.assertIsNone(listener.silence_started)
        self.assertIsNone(listener.speech_started)
        self.assertEqual(listener.longest_voiced_run, 0)
