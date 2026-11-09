"""Passive wake learning: what is collected, when it retrains, when a new model is kept, and that it all stays local."""
import os, stat, tempfile, unittest
from pathlib import Path
import numpy as np
from wake import personal as wp
import julius_wake

def tmpdir():
    d = tempfile.mkdtemp(); import atexit, shutil; atexit.register(shutil.rmtree, d, True); return Path(d)

def clip(n=16000, v=0.1): return np.full(n, v, np.float32)
def event(accepted, reason="", c=True): return {"accepted": accepted, "reason": reason if not accepted else "", "clip": clip() if c else None}

class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t

class Collect(unittest.TestCase):
    def setUp(self):
        self.store = wp.Store(tmpdir() / "wp"); self.clock = Clock(); self.spawned = []
        self.l = wp.Learner(self.store, learn_after=3, spawn=lambda s: self.spawned.append(1), clock=self.clock)
    def test_confirmed_wake_is_a_positive(self):
        self.l.observe(event(True)); self.assertEqual(len(self.store.positives()), 1); self.assertEqual(len(self.store.negatives()), 0)
    def test_rejected_then_repeated_wake_gives_two_positives_and_no_negative(self):
        self.l.observe(event(False, "whisper")); self.clock.t += 3; self.l.observe(event(True))
        self.assertEqual(len(self.store.positives()), 2); self.l.flush(self.clock.t + 20); self.assertEqual(len(self.store.negatives()), 0)
    def test_rejected_and_never_repeated_becomes_a_negative_after_the_window(self):
        self.l.observe(event(False, "whisper")); self.l.flush(self.clock.t + 2); self.assertEqual(len(self.store.negatives()), 0)
        self.l.flush(self.clock.t + 6); self.assertEqual(len(self.store.negatives()), 1)
    def test_two_rejections_in_a_row_keep_the_first_as_a_negative(self):
        self.l.observe(event(False, "whisper")); self.clock.t += 8; self.l.observe(event(False, "whisper")); self.assertEqual(len(self.store.negatives()), 1)
    def test_speaker_rejections_are_not_training_data(self):
        self.l.observe(event(False, "speaker")); self.l.flush(self.clock.t + 99); self.assertEqual((len(self.store.positives()), len(self.store.negatives())), (0, 0))
    def test_clips_without_audio_are_ignored_and_errors_never_escape(self):
        self.l.observe(event(True, c=False)); self.assertEqual(len(self.store.positives()), 0)
        self.l.store = None; self.l.observe(event(True))   # would raise inside; must not propagate
    def test_retrain_starts_after_n_new_positives_once(self):
        for i in range(2): self.clock.t += 10; self.l.observe(event(True))
        self.assertEqual(self.spawned, []); self.clock.t += 10; self.l.observe(event(True)); self.assertEqual(len(self.spawned), 1)
        (self.store.dir / "training.lock").write_text("1"); self.clock.t += 10; self.l.observe(event(True)); self.assertEqual(len(self.spawned), 1)   # not while one runs
    def test_counter_restarts_after_a_training_run(self):
        for i in range(3): self.clock.t += 10; self.l.observe(event(True))
        self.store.save_state(positives_at_last_train=3); self.assertEqual(self.store.new_positives(), 0)
        self.clock.t += 10; self.l.observe(event(True)); self.assertEqual(self.store.new_positives(), 1); self.assertEqual(len(self.spawned), 1)

class Storage(unittest.TestCase):
    def test_private_permissions_and_caps_and_forget(self):
        s = wp.Store(tmpdir() / "wp")
        for i in range(wp.MAX_NEG + 5): s.add_negative(clip(), when=1000 + i)
        self.assertEqual(len(s.negatives()), wp.MAX_NEG)
        p = s.add_positive(clip(16000 * 10), "confirmed", 5)
        self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600); self.assertEqual(stat.S_IMODE(s.dir.stat().st_mode), 0o700)
        import soundfile as sf; self.assertLessEqual(sf.info(p).duration, wp.MAX_SECONDS + 0.01)
        s.save_state(learn_after=7); self.assertEqual(s.status()["needed_for_next_training"], 7)
        s.forget(); self.assertFalse(s.dir.exists())
    def test_default_location_is_local_and_overridable(self):
        os.environ["JULIUS_WAKE_PERSONAL"] = "/tmp/x_wp"
        try: self.assertEqual(wp.home(), Path("/tmp/x_wp"))
        finally: os.environ.pop("JULIUS_WAKE_PERSONAL")
        self.assertTrue(str(wp.home()).endswith("/.julius/wake_personal"))
    def test_nothing_is_uploaded_and_the_folder_is_gitignored_by_location(self):
        src = (Path(wp.__file__)).read_text()
        for bad in ("requests", "urllib", "http.client", "socket", "anthropic"): self.assertNotIn("import " + bad, src)
        self.assertTrue(str(wp.home()).startswith(str(Path.home() / ".julius")))   # outside the repository

class Keep(unittest.TestCase):
    def test_decision_rules(self):
        d = wp.decide
        self.assertTrue(d({"recall": .6, "false_rate": .1}, {"recall": .8, "false_rate": .1})[0])
        self.assertFalse(d({"recall": .8, "false_rate": .1}, {"recall": .8, "false_rate": .1})[0])
        self.assertFalse(d({"recall": .8, "false_rate": .1}, {"recall": .82, "false_rate": .1})[0])   # a gain inside the noise is not enough
        self.assertTrue(d({"recall": .8, "false_rate": .3}, {"recall": .8, "false_rate": .1})[0])
        self.assertFalse(d({"recall": .6, "false_rate": .0}, {"recall": 1.0, "false_rate": .5})[0])   # better recall but far more false fires
        self.assertTrue(d({"recall": .8, "false_rate": .12}, {"recall": 1.0, "false_rate": .24})[0])   # a few extra Whisper checks for 20 points of recall
        self.assertFalse(d({"recall": .9, "false_rate": .1}, {"recall": .7, "false_rate": .0})[0])
    def test_too_few_clips_does_not_train(self):
        s = wp.Store(tmpdir() / "wp")
        for i in range(3): s.add_positive(clip(), "confirmed", i)
        r = wp.retrain(s)
        self.assertFalse(r["kept"]); self.assertFalse(s.model.exists())

class Setup(unittest.TestCase):
    def test_up_to_five_clips_and_skippable(self):
        s = wp.Store(tmpdir() / "wp"); said = []
        n = wp.setup(s, count=5, record=lambda: clip(40000, .2), ask=lambda p: "", say=said.append); self.assertEqual((n, len(s.positives())), (5, 5))
        s2 = wp.Store(tmpdir() / "wp2"); answers = iter(["", "", "s"])
        n = wp.setup(s2, count=5, record=lambda: clip(40000, .2), ask=lambda p: next(answers), say=lambda m: None); self.assertEqual(n, 2)
        s3 = wp.Store(tmpdir() / "wp3"); n = wp.setup(s3, record=lambda: clip(40000, .2), ask=lambda p: "s", say=lambda m: None); self.assertEqual((n, len(s3.positives())), (0, 0))
    def test_silent_takes_are_not_saved(self):
        s = wp.Store(tmpdir() / "wp"); n = wp.setup(s, count=2, record=lambda: clip(40000, 0.0), ask=lambda p: "", say=lambda m: None); self.assertEqual(n, 0)

class ModelChoice(unittest.TestCase):
    def test_explicit_model_wins_without_learning(self):
        m, thr, learns = julius_wake.choose_model({"model": "wake/models/julius.onnx", "threshold_low": 0.05}, root=Path("/r"), personal_dir=tmpdir())
        self.assertEqual((m, thr, learns), (Path("/r/wake/models/julius.onnx"), 0.05, False))
    def test_starter_is_the_default_and_threshold_is_low(self):
        m, thr, learns = julius_wake.choose_model({"model": "", "starter_threshold": 0.0005, "threshold_low": 0.05}, root=Path("/r"), personal_dir=tmpdir())
        self.assertEqual((m.name, thr, learns), ("julius_starter.onnx", 0.0005, True))
    def test_personal_model_replaces_the_starter_once_it_exists(self):
        d = tmpdir(); (d / "model.onnx").write_bytes(b"x")
        m, thr, learns = julius_wake.choose_model({"model": "", "starter_threshold": 0.0005}, root=Path("/r"), personal_dir=d)
        self.assertEqual(m, d / "model.onnx"); self.assertTrue(learns)
    def test_learning_can_be_switched_off(self):
        self.assertFalse(julius_wake.choose_model({"model": "", "learn": False}, root=Path("/r"), personal_dir=tmpdir())[2])
    def test_starter_files_ship(self):
        root = Path(julius_wake.__file__).parent
        for f in ("julius_starter.onnx", "julius_starter_head.pt"): self.assertTrue((root / "wake" / "models" / f).exists(), f)
        ign = (root / ".gitignore").read_text(); self.assertIn("!wake/models/julius_starter.onnx", ign); self.assertIn("!wake/models/julius_starter_head.pt", ign)

class EngineHooks(unittest.TestCase):
    class Stream:
        speaker_threshold = .25
        def __init__(self, name): self.name = name; self.events = []
        def push(self, f): return self.events
        def reset(self): pass
    def test_engine_feeds_the_learner_and_swaps_in_a_reloaded_stream(self):
        seen = []
        class L:
            def observe(s, e): seen.append(e)
            def flush(s): seen.append("flush")
        new = self.Stream("new"); calls = iter([None, new])
        eng = julius_wake.WakeEngine(self.Stream("old"), tmpdir(), learner=L(), reloader=lambda: next(calls))
        eng.stream.events = [{"accepted": True, "score": .9, "speaker": None, "heard": "julius", "verify_s": .1, "request": "", "clip": clip(), "reason": ""}]
        eng.feed(np.zeros(1280, np.float32)); self.assertEqual(len([e for e in seen if isinstance(e, dict)]), 1)
        eng.reset(); self.assertEqual(eng.stream.name, "old"); eng.reset(); self.assertEqual(eng.stream.name, "new"); self.assertIn("flush", seen)
    def test_a_failing_reloader_never_breaks_the_engine(self):
        def boom(): raise RuntimeError("x")
        eng = julius_wake.WakeEngine(self.Stream("old"), tmpdir(), reloader=boom); eng.reset(); self.assertEqual(eng.stream.name, "old")

if __name__ == "__main__": unittest.main()
