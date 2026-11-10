import tempfile
import unittest
from pathlib import Path

import numpy as np

import julius.audio.session_record as sr


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.d, ignore_errors=True))
        self.now = [1000.0]

    def rec(self):
        return sr.SessionRecorder(self.d / "c1", clock=lambda: self.now[0], meta={"scenario": "x"})

    def test_mic_and_playback_share_a_timeline_and_events_are_kept(self):
        r = self.rec()
        for _ in range(16):
            r.mic(np.full(1000, 0.1, dtype=np.float32))  # 1.0 s of mic
        self.now[0] += 0.5
        r.out(np.full(8000, 0.5, dtype=np.float32), 16000)  # 0.5 s tone starting at 0.5 s
        self.now[0] += 0.5
        r.out(np.full(12000, -0.5, dtype=np.float32), 24000)  # 0.5 s at 24 kHz, queued right behind
        r.event("turn", n=1, text="hello")
        r.close()
        mic, rate = sr.read_wav(self.d / "c1" / "mic.wav")
        out, _ = sr.read_wav(self.d / "c1" / "out.wav")
        self.assertEqual(rate, 16000)
        self.assertEqual(len(mic), len(out))
        self.assertAlmostEqual(mic[100], 0.1, places=2)
        self.assertAlmostEqual(float(out[4000]), 0.0, places=3)
        self.assertAlmostEqual(float(out[8100]), 0.5, places=2)
        self.assertAlmostEqual(
            float(out[16500]), -0.5, places=2
        )  # second chunk follows the first, not the wall clock gap
        kinds = [e["kind"] for e in sr.read_events(self.d / "c1")]
        self.assertEqual(kinds, ["session_start", "turn", "session_end"])
        self.assertFalse((self.d / "c1" / "mic.raw").exists())

    def test_recording_never_raises_into_the_audio_callback(self):
        r = self.rec()
        r.mic(None)
        r.out("not audio", 16000)
        r.close()
        r.mic(np.zeros(10, dtype=np.float32))
        r.out(np.zeros(10, dtype=np.float32), 16000)  # after close too

    def test_off_without_the_environment_variable(self):
        import os

        os.environ.pop("JULIUS_SESSION_RECORD_DIR", None)
        self.assertIsNone(sr.from_env())

    def test_a_crashed_session_can_be_finalized(self):
        r = self.rec()
        r.mic(np.full(1600, 0.2, dtype=np.float32))
        r._mic.flush()
        sr.finalize(self.d / "c1")
        mic, _ = sr.read_wav(self.d / "c1" / "mic.wav")
        self.assertEqual(len(mic), 1600)


if __name__ == "__main__":
    unittest.main()
