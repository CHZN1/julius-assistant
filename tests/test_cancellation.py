import subprocess
import sys
import threading
import time
import unittest

from julius.audio.cancellation import Cancellation, TurnCancelled
from julius.audio.tts import SpeechEngine


class CancellationTests(unittest.TestCase):
    def test_nested_activity_preserves_stop_and_clears_next_turn(self):
        c = Cancellation()
        self.assertFalse(c.request())
        with c.activity():
            self.assertTrue(c.request())
            with c.activity():
                with self.assertRaises(TurnCancelled):
                    c.check()
            self.assertTrue(c.event.is_set())
        with c.activity():
            c.check()
        self.assertFalse(c.busy.is_set())

    def test_stop_interrupts_waiting_worker_without_waiting_for_response(self):
        e = SpeechEngine.__new__(SpeechEngine)
        e._worker_buffer = b""
        e.cancel_event = threading.Event()
        e.worker = subprocess.Popen(
            [sys.executable, "-c", "import time;time.sleep(30)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        timer = threading.Timer(0.05, e.cancel_event.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(TurnCancelled):
                e._worker_line(10)
            self.assertLess(time.monotonic() - started, 1)
            self.assertIsNotNone(e.worker.poll())
        finally:
            timer.cancel()
            if e.worker.poll() is None:
                e.worker.kill()
                e.worker.wait()
            e.worker.stdin.close()
            e.worker.stdout.close()
