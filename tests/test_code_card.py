"""The coding task's changes as a panel card: the diff of the scratch copy and the card's validation."""

import tempfile
import types
import unittest
from pathlib import Path

import julius.agents.code_agent as ca
from julius.ui_bridge import events as ev
from julius.ui_bridge import panel as pn


class CodeCardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name) / "work"
        self.work.mkdir()
        (self.work / "a.py").write_text("x = 1\n")
        ca._git(self.work, "init", "-q")
        ca._git(self.work, "add", "-A")
        ca._git(self.work, "commit", "-q", "-m", "baseline")

    def test_diff_lines_show_new_and_changed_files_without_git_headers(self):
        (self.work / "a.py").write_text("x = 2\n")
        (self.work / "rename_exports.py").write_text("print('hi')\n")
        lines = ca.diff_lines(types.SimpleNamespace(work=self.work))
        self.assertIn("-x = 1", lines)
        self.assertIn("+x = 2", lines)
        self.assertIn("+print('hi')", lines)
        self.assertIn("+++ b/rename_exports.py", lines)
        self.assertFalse([ln for ln in lines if ln.startswith(("diff --git", "index "))])

    def test_diff_leaves_out_caches_and_binary_files(self):
        (self.work / "a.py").write_text("x = 2\n")
        cache = self.work / "tests" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "test_a.cpython-312.pyc").write_bytes(b"\x00\x01binary\x00")
        (self.work / "stray.pyc").write_bytes(b"\x00\x02")
        (self.work / ".pytest_cache").mkdir()
        (self.work / ".pytest_cache" / "README.md").write_text("cache\n")
        (self.work / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
        task = types.SimpleNamespace(work=self.work)
        lines = ca.diff_lines(task)
        self.assertIn("+x = 2", lines)
        joined = "\n".join(lines)
        for unwanted in ("__pycache__", ".pyc", ".pytest_cache", "Binary files"):
            self.assertNotIn(unwanted, joined)
        self.assertEqual(ca.diff_stat(task), (1, 1))

    def test_the_card_is_valid_and_cut(self):
        hub = pn.PanelHub(ev.EventHub())
        pid = hub.code(
            {
                "title": "Code: export-tools",
                "project": "export-tools",
                "summary": "Changed 1 new file.",
                "files": [
                    {"path": "rename_exports.py", "status": "added"},
                    {"path": "x", "status": "bogus"},
                ],
                "diff": ["+line"] * 500,
                "tests": "The agent reports: all 4 tests pass.",
            }
        )
        self.assertTrue(pid)
        card = hub.cards[pid]
        self.assertEqual(len(card["diff"]), pn.MAX_DIFF_LINES)
        self.assertEqual([f["path"] for f in card["files"]], ["rename_exports.py"])
        self.assertIn("panel.code", pn.CARD_TYPES)


if __name__ == "__main__":
    unittest.main()
