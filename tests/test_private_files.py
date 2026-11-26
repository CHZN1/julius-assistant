"""Owner-only files (threat model L1): Julius creates its data 0600/0700 and tightens what earlier versions left readable."""

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from julius import private


def mode(p):
    return stat.S_IMODE(os.lstat(p).st_mode)


class OwnerOnly(unittest.TestCase):
    def test_files_created_under_the_umask_are_owner_only(self):
        code = (
            "import os, sys; from julius import private; private.owner_only_umask();"
            "from julius.memory.store import MemoryStore; from julius.costs import CostTracker;"
            "from types import SimpleNamespace as N; d = sys.argv[1];"
            "MemoryStore(os.path.join(d, 'mem', 'memory.sqlite3'));"
            "CostTracker(os.path.join(d, 'costs.jsonl'), 5).record('haiku', N(input_tokens=1, output_tokens=1));"
            "open(os.path.join(d, 'julius.jsonl'), 'a').write('x')"
        )
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(
                [sys.executable, "-c", code, d], check=True, cwd=Path(__file__).resolve().parents[1]
            )
            for f in ("mem/memory.sqlite3", "costs.jsonl", "julius.jsonl"):
                with self.subTest(f):
                    self.assertEqual(mode(Path(d) / f), 0o600)
            self.assertEqual(mode(Path(d) / "mem"), 0o700)

    def test_existing_readable_files_are_tightened_without_losing_your_access(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "state"
            (root / "sessions").mkdir(parents=True)
            (root / "env-service" / "bin").mkdir(parents=True)
            files = {
                root / "memory.sqlite3": 0o644,
                root / "sessions" / "2026-11-25.jsonl": 0o664,
                root / "tool.sh": 0o755,  # stays executable for you
                root / "env-service" / "bin" / "python": 0o755,  # not walked
            }
            for f, m in files.items():
                f.write_text("x")
                os.chmod(f, m)
            os.chmod(root, 0o755)
            os.chmod(root / "sessions", 0o755)
            outside = Path(d) / "elsewhere.txt"
            outside.write_text("x")
            os.chmod(outside, 0o644)
            (root / "link").symlink_to(outside)
            n = private.secure_existing([root])
            self.assertEqual(mode(root / "memory.sqlite3"), 0o600)
            self.assertEqual(mode(root / "sessions" / "2026-11-25.jsonl"), 0o600)
            self.assertEqual(mode(root / "tool.sh"), 0o700)
            self.assertEqual(mode(root), 0o700)
            self.assertEqual(mode(root / "sessions"), 0o700)
            self.assertEqual(mode(root / "env-service" / "bin" / "python"), 0o755)
            self.assertEqual(
                mode(outside), 0o644
            )  # a symlink is never followed out of the data folder
            self.assertGreaterEqual(n, 5)
            self.assertEqual(private.secure_existing([root]), 0)  # nothing left to change

    def test_only_data_folders_are_ever_walked(self):
        repo = Path(__file__).resolve().parents[1]
        for bad in (Path.home(), Path("/"), Path("."), repo, Path("relative/dir")):
            with self.subTest(str(bad)):
                self.assertFalse(private.safe_root(bad))
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(private.safe_root(Path(d)))
        from unittest import mock

        from julius import state

        with mock.patch.dict(
            os.environ, {"JULIUS_STATE_DIR": ""}
        ):  # empty means unset, never "here"
            self.assertEqual(state.state_dir(), Path("~/.julius").expanduser())

    def test_the_launch_agent_sets_an_owner_only_umask(self):
        from julius.service import service

        self.assertEqual(service.plist_dict()["Umask"], 0o077)


if __name__ == "__main__":
    unittest.main()
