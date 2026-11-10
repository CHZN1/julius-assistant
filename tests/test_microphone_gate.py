"""Unit tests for microphone gate."""

import unittest
from unittest.mock import Mock, patch

import numpy as np

from julius.audio.mic_gate import MicrophoneGate
from julius.runtime import capture, speech
from julius.runtime.listen import Listener
from tests.harness import make_session


class GateTests(unittest.TestCase):
    def test_playback_tail_and_failure_are_muted(self):
        now = [10.0]
        gate = MicrophoneGate(clock=lambda: now[0])
        self.assertTrue(gate.accepting())
        with self.assertRaises(RuntimeError):
            with gate.output():
                self.assertFalse(gate.accepting())
                with gate.output():
                    self.assertFalse(gate.accepting())
                self.assertFalse(gate.accepting())
                raise RuntimeError("failed playback")
        self.assertFalse(gate.accepting())
        now[0] += 0.36
        self.assertTrue(gate.accepting())

    def test_callback_drops_playback_and_retains_user_audio(self):
        now = [10.0]
        s = make_session(microphone_gate=MicrophoneGate(clock=lambda: now[0]))
        audio = np.ones((100, 1))
        with patch.object(capture.time, "perf_counter", lambda: now[0]):
            with s.microphone_gate.output():
                capture.callback(s, audio, 100, None, None)
            capture.callback(s, audio, 100, None, None)
            self.assertFalse(s.chunks)
            now[0] += 0.36
            capture.callback(s, audio, 100, None, None)
        self.assertEqual(len(s.chunks), 1)
        samples, captured_at = s.chunks[0]
        self.assertEqual(captured_at, now[0])
        audio[:] = 0
        self.assertTrue(np.all(samples == 1))

    def test_speech_failure_releases_gate(self):
        now = [10.0]
        s = make_session(microphone_gate=MicrophoneGate(clock=lambda: now[0]))
        s.tts.synthesize.side_effect = RuntimeError("voice failed")
        with patch.object(speech, "close_backend", Mock()), self.assertRaises(RuntimeError):
            speech.speak_sentence(s, "Hello.")
        self.assertFalse(s.microphone_gate.accepting())
        now[0] += 0.36
        self.assertTrue(s.microphone_gate.accepting())

    def test_capture_gap_resets_wake_engine(self):
        """Regression: audio around Julius's own playback must not be stitched into one wake
        candidate (the wake stream is reset on every capture gap)."""
        s = make_session(wake_engine=Mock(), capture_clock=Mock())
        s.capture_clock.discontinuous.return_value = True
        s.chunks.append((np.zeros(1024, dtype=np.float32), 1.0))
        with patch("builtins.print"):
            Listener(s).process_chunk(awake=True)
        s.wake_engine.reset.assert_called_once()


if __name__ == "__main__":
    unittest.main()
