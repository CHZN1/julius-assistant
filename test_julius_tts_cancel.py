"""A reply abandoned mid-stream (the user spoke over Julius) must not leave stale chunk messages for the next reply (live run 11-07: 'No such file ... 3.npy', every later reply silent)."""
import json, os, subprocess, sys, tempfile, textwrap, time, unittest
from pathlib import Path

FAKE = textwrap.dedent('''
    import json, signal, sys, time
    flag = [False]
    signal.signal(signal.SIGUSR1, lambda s, f: flag.__setitem__(0, True))
    print(json.dumps({"ready": True}), flush=True)
    for line in sys.stdin:
        req = json.loads(line); flag[0] = False; n = 0
        for i in range(60):
            if flag[0]: break
            print(json.dumps({"chunk": True, "path": req["text"] + "-" + str(i), "sample_rate": 24000}), flush=True); n += 1; time.sleep(0.02)
        print(json.dumps({"done": True, "cancelled": flag[0], "chunks": n}), flush=True)
''')


def make_engine(script, hang=False):
    import julius_tts
    e = julius_tts.SpeechEngine.__new__(julius_tts.SpeechEngine)
    e.worker = subprocess.Popen([sys.executable, "-u", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    e._worker_buffer = b""; e.cancel_event = None
    e._worker_line(timeout=10)   # ready
    return e


class AbandonedStream(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.script = os.path.join(self.dir, "fake_worker.py"); Path(self.script).write_text(FAKE)

    def test_next_reply_starts_clean_after_an_abandoned_one(self):
        e = make_engine(self.script)
        try:
            e.worker.stdin.write(json.dumps({"text": "first"}) + "\n"); e.worker.stdin.flush()
            self.assertIn("first-0", e._worker_line(timeout=5)); e._worker_line(timeout=5)   # consumer reads two chunks, then the user interrupts
            t0 = time.time(); e._abandon_stream(); self.assertLess(time.time() - t0, 2.0, "must not wait for the whole reply to be generated")
            self.assertIsNotNone(e.worker, "the loaded worker is kept")
            e.worker.stdin.write(json.dumps({"text": "second"}) + "\n"); e.worker.stdin.flush()
            self.assertEqual(json.loads(e._worker_line(timeout=5))["path"], "second-0", "the next reply must not see leftovers of the first")
        finally:
            e.worker.kill()

    def test_unresponsive_worker_is_replaced_not_trusted(self):
        hang = FAKE.replace("signal.signal(signal.SIGUSR1, lambda s, f: flag.__setitem__(0, True))", "signal.signal(signal.SIGUSR1, signal.SIG_IGN)").replace("if flag[0]: break", "pass")
        Path(self.script).write_text(hang)
        import julius_tts
        e = make_engine(self.script)
        e.worker.stdin.write(json.dumps({"text": "x"}) + "\n"); e.worker.stdin.flush(); e._worker_line(timeout=5)
        real = time.monotonic; start = real()
        import unittest.mock as m
        with m.patch("time.monotonic", lambda: real() + (real() - start) * 8):   # compress the 10 s acknowledgement window
            e._abandon_stream()
        self.assertIsNone(e.worker, "a worker that does not acknowledge the stop is terminated so the next reply starts a fresh one")


if __name__ == "__main__":
    unittest.main()
