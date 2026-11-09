import json, tempfile, unittest
from pathlib import Path
import numpy as np
import julius_wake as jw


class FakeStream:
    speaker = None; speaker_threshold = 0.25
    def __init__(self, events_by_call=None): self.frames = []; self.scripted = events_by_call or {}; self.resets = 0
    def push(self, frame): self.frames.append(len(frame)); return self.scripted.get(len(self.frames), [])
    def reset(self): self.resets += 1


def ev(accepted, score=0.9, speaker=None, request="", clip=None, reason=""):
    return {"fire": 1.0, "decided": 2.0, "accepted": accepted, "reason": reason, "heard": request, "request": request, "verify_s": 0.24, "score": score, "speaker": speaker,
            "clip": clip if clip is not None else np.zeros(16000, np.float32)}


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class WakeEngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(); self.clock = Clock()
    def tearDown(self): self.dir.cleanup()
    def engine(self, stream): return jw.WakeEngine(stream, self.dir.name, clock=self.clock)
    def rows(self): return [json.loads(l) for l in (Path(self.dir.name) / "wake_log.jsonl").read_text().splitlines()]
    def clips(self): return sorted((Path(self.dir.name) / "wake_clips").glob("*.wav")) if (Path(self.dir.name) / "wake_clips").exists() else []

    def test_chunks_of_any_size_become_80ms_frames(self):
        stream = FakeStream(); e = self.engine(stream)
        e.feed(np.zeros(1024, np.float32)); self.assertEqual(stream.frames, [])
        e.feed(np.zeros(1024, np.float32)); e.feed(np.zeros(4000, np.float32))
        self.assertEqual(stream.frames, [1280] * 4)  # 1024+1024+4000 = 6048 samples = 4 frames plus 928 carried over
    def test_accepted_event_is_returned_and_logged_without_saving_a_clip(self):
        e = self.engine(FakeStream({1: [ev(True, request="what time is it")]}))
        out = e.feed(np.zeros(1280, np.float32))
        self.assertEqual(out[0]["request"], "what time is it"); self.assertEqual(self.rows()[0]["accepted"], True); self.assertEqual(self.clips(), [])
    def test_rejected_fire_is_logged_but_returns_nothing(self):
        e = self.engine(FakeStream({1: [ev(False, reason="whisper", request="Julia said")]}))
        self.assertEqual(e.feed(np.zeros(1280, np.float32)), []); row = self.rows()[0]
        self.assertEqual((row["accepted"], row["reason"], row["repeat"]), (False, "whisper", False)); self.assertEqual(self.clips(), [])
    def test_a_fire_within_five_seconds_of_a_rejection_saves_the_rejected_clip(self):
        e = self.engine(FakeStream({1: [ev(False, reason="speaker", speaker=0.1)], 2: [ev(True, request="hello")]}))
        e.feed(np.zeros(1280, np.float32)); self.clock.t += 3.0; e.feed(np.zeros(1280, np.float32))
        rows = self.rows(); self.assertTrue(rows[1]["repeat"]); self.assertEqual(rows[1]["gap_s"], 3.0); self.assertIn("near_miss_clip", rows[1]); self.assertEqual(len(self.clips()), 1)
    def test_a_fire_after_five_seconds_is_not_a_repeat(self):
        e = self.engine(FakeStream({1: [ev(False, reason="whisper")], 2: [ev(True)]}))
        e.feed(np.zeros(1280, np.float32)); self.clock.t += 6.0; e.feed(np.zeros(1280, np.float32))
        self.assertFalse(self.rows()[1]["repeat"]); self.assertEqual(self.clips(), [])
    def test_borderline_verifier_accept_saves_a_clip(self):
        stream = FakeStream({1: [ev(True, speaker=0.3)]}); stream.speaker = object()
        self.engine(stream).feed(np.zeros(1280, np.float32)); self.assertEqual(len(self.clips()), 1)
    def test_confident_verifier_accept_saves_nothing(self):
        stream = FakeStream({1: [ev(True, speaker=0.9)]}); stream.speaker = object()
        self.engine(stream).feed(np.zeros(1280, np.float32)); self.assertEqual(self.clips(), [])
    def test_wake_word_said_again_while_awake_is_logged_with_its_gap(self):
        e = self.engine(FakeStream({1: [ev(True)]})); e.feed(np.zeros(1280, np.float32)); self.clock.t += 2.0; e.note_spoken_wake("Julius, what about tomorrow")
        self.assertEqual((self.rows()[1]["event"], self.rows()[1]["repeat"]), ("wake_while_awake", True))
    def test_reset_clears_buffered_audio_and_the_stream(self):
        stream = FakeStream(); e = self.engine(stream); e.feed(np.zeros(1000, np.float32)); e.reset(); e.feed(np.zeros(300, np.float32))
        self.assertEqual((stream.frames, stream.resets), ([], 1))


class FromConfigTests(unittest.TestCase):
    def test_whisper_engine_means_no_cascade(self):
        self.assertIsNone(jw.from_config(lambda c: (False, "", 0), "x", {"wake": {"engine": "whisper"}}, say=lambda m: None))
    def test_cascade_builds_without_a_verifier_file(self):
        said = []
        e = jw.from_config(lambda c: (False, "", 0), tempfile.gettempdir(), {"wake": {"engine": "cascade", "verifier": "/nonexistent/v.pkl"}}, say=said.append)
        self.assertIsNotNone(e); self.assertIsNone(e.stream.speaker); self.assertIn("+ Whisper check", said[0])
    def test_silence_is_not_scored_by_the_detector(self):
        e = jw.from_config(lambda c: (False, "", 0), tempfile.gettempdir(), {"wake": {"engine": "cascade", "verifier": "/nonexistent/v.pkl"}}, say=lambda m: None)
        rng = np.random.default_rng(0)
        for _ in range(60): e.feed((rng.standard_normal(1280) * 0.002).astype(np.float32))
        self.assertLess(e.stream.scored, 30)  # after the first 1.6 s the gate skips the detector


if __name__ == "__main__":
    unittest.main()
