"""Unit tests for smoke."""

import json
import unittest

from julius.service.smoke import check, render


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def log(*msgs):
    return [json.dumps({"ts": "2026-11-08T10:00:00-0800", "msg": m}) for m in msgs]


GOOD = log(
    "[duplex audio: 48000 Hz]",
    "[hotkey <ctrl>+<alt>+s active]",
    "[hotkey <ctrl>+<alt>+j active]",
    "julius ready · cloud",
)
START = "2026-11-08T09:59:00"


def run(state, lines, err="", **kw):
    c = Clock()
    return check(
        state, lambda: lines, lambda: err, START, clock=c, sleep=c.sleep, timeout=10, stable=5, **kw
    )


class SmokeTests(unittest.TestCase):
    def test_healthy_start_passes(self):
        ok, res = run(lambda: {"state": "running", "pid": "10", "runs": "1"}, GOOD)
        self.assertTrue(ok, res)

    def test_crash_loop_changes_pid_and_fails(self):
        pids = iter(["10", "10", "11", "11", "11"])

        def state():
            return {"state": "running", "pid": next(pids), "runs": "2"}

        ok, res = run(state, GOOD)
        self.assertFalse(ok)
        self.assertIn("stable", [n for n, r, _ in res if not r][0])

    def test_never_ready_fails(self):
        ok, res = run(
            lambda: {"state": "running", "pid": "10", "runs": "1"},
            log("[hotkey <ctrl>+<alt>+s active]"),
        )
        self.assertFalse(ok)
        self.assertIn("julius ready", [n for n, r, _ in res if not r])

    def test_missing_hotkey_fails(self):
        ok, res = run(
            lambda: {"state": "running", "pid": "10", "runs": "1"},
            log("[duplex audio]", "[hotkey <ctrl>+<alt>+s active]", "julius ready"),
        )
        self.assertFalse(ok)

    def test_traceback_fails(self):
        ok, res = run(
            lambda: {"state": "running", "pid": "10", "runs": "1"},
            GOOD,
            err="x\nTraceback (most recent call last):\n  boom",
        )
        self.assertFalse(ok)

    def test_old_log_lines_before_restart_are_ignored(self):
        old = [json.dumps({"ts": "2026-11-08T08:00:00-0800", "msg": "julius ready"})]
        ok, res = run(lambda: {"state": "running", "pid": "10", "runs": "1"}, old)
        self.assertFalse(ok)

    def test_duplex_line_only_required_when_on(self):
        lines = log(
            "[hotkey <ctrl>+<alt>+s active]", "[hotkey <ctrl>+<alt>+j active]", "julius ready"
        )
        self.assertTrue(
            run(lambda: {"state": "running", "pid": "10", "runs": "1"}, lines, duplex=False)[0]
        )

    def test_render(self):
        self.assertIn(
            "SMOKE PASS", render(*run(lambda: {"state": "running", "pid": "10", "runs": "1"}, GOOD))
        )


if __name__ == "__main__":
    unittest.main()
