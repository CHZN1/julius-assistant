"""Unit tests for hotkey."""

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from julius.service import hotkey


def fake_popen(lines):
    """A helper process that prints `lines` then stays open until stdin closes."""
    code = f"import sys,time\nfor l in {lines!r}: print(l,flush=True)\nsys.stdin.read()"
    return lambda cmd, **kw: subprocess.Popen([sys.executable, "-c", code], **kw)


class HotkeyTests(unittest.TestCase):
    def test_press_calls_stop_and_ready_is_reported(self):
        calls = []
        out = []
        h = hotkey.start(
            lambda: calls.append(1),
            "<ctrl>+<alt>+s",
            say=out.append,
            popen=fake_popen(["ready key", "fire key", "fire key"]),
            binary="x",
        )
        try:
            for _ in range(50):
                if len(calls) == 2:
                    break
                time.sleep(0.05)
            self.assertEqual(calls, [1, 1])
            self.assertIn("active", out[0])
        finally:
            h.stop()

    def test_registration_failure_is_unavailable_not_fatal(self):
        out = []
        self.assertIsNone(
            hotkey.start(
                lambda: None,
                say=out.append,
                popen=fake_popen(["failed key status=-9878", "ready"]),
                binary="x",
            )
        )
        self.assertIn("unavailable", out[0])
        self.assertIn("-9878", out[0])

    def test_helper_that_exits_is_unavailable(self):
        out = []
        self.assertIsNone(
            hotkey.start(
                lambda: None,
                say=out.append,
                popen=lambda cmd, **kw: subprocess.Popen([sys.executable, "-c", "pass"], **kw),
                binary="x",
            )
        )
        self.assertIn("unavailable", out[0])

    def test_missing_compiler_or_binary_is_reported(self):
        out = []

        def boom(*a, **k):
            raise FileNotFoundError("clang")

        self.assertIsNone(hotkey.start(lambda: None, say=out.append, popen=boom, binary="x"))
        self.assertIn("unavailable", out[0])

    def test_build_compiles_when_missing_and_skips_when_fresh(self):
        d = Path(tempfile.mkdtemp())
        src = d / "h.c"
        src.write_text("int main(){return 0;}")
        b = d / "bin" / "h"

        def runner(cmd, **kw):
            b.write_text("x")
            return Mock(returncode=0, stderr="")

        r = Mock(side_effect=runner)
        hotkey.build(src, b, r)
        hotkey.build(src, b, r)
        self.assertEqual(r.call_count, 1)
        self.assertIn("Carbon", r.call_args[0][0])

    def test_build_failure_raises_with_output(self):
        d = Path(tempfile.mkdtemp())
        src = d / "h.c"
        src.write_text("x")
        with self.assertRaises(RuntimeError):
            hotkey.build(src, d / "h", lambda *a, **k: Mock(returncode=1, stderr="boom"))

    def test_real_helper_registers_both_combos(self):
        import shutil

        if not shutil.which("clang") and not Path("/usr/bin/clang").exists():
            self.skipTest("no compiler")
        b = hotkey.build()
        p = subprocess.Popen(
            [str(b), "a=ctrl+alt+s", "b=ctrl+alt+j", "c=ctrl+alt+nonsense"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            lines = [p.stdout.readline().strip() for _ in range(2)]
            self.assertIn("failed c unknown-key", lines)
            self.assertIn("ready a b", lines)
        finally:
            p.stdin.close()
            p.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
