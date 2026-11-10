from julius.paths import REPO_ROOT
import unittest
from julius.brain.undo import UndoCoordinator, UndoAgent


def make():
    s = {"files": 0, "memory": 0, "code": 0, "done": []}
    def agent(name, label, msg):
        return UndoAgent(label, lambda: s[name], lambda: s[name] > 0, lambda: (s.__setitem__(name, 0), s["done"].append(name), msg)[2])
    c = UndoCoordinator({"files": agent("files", "the last file change", "Moved 2 files back."), "code": agent("code", "the coding merge", "Restored 3 files."), "memory": agent("memory", "your last forget", "I've put it back.")})
    return s, c


class UndoTests(unittest.TestCase):
    def test_most_recent_agent_wins_and_is_named(self):
        s, c = make(); s["memory"] = 1; c.observe(1); s["files"] = 1; c.observe(2)
        self.assertEqual(c.handle("undo"), "Undid the last file change. Moved 2 files back.")
        self.assertEqual(c.handle("undo that"), "Undid your last forget. I've put it back.", "repeats down the stack")
        self.assertIsNone(c.handle("undo"), "nothing left")

    def test_memory_after_files(self):
        s, c = make(); s["files"] = 1; c.observe(1); s["memory"] = 1; c.observe(2)
        self.assertTrue(c.handle("please undo it").startswith("Undid your last forget"))

    def test_coding_merge_most_recent(self):
        s, c = make(); s["files"] = 1; c.observe(1); s["code"] = 1; c.observe(2)
        self.assertTrue(c.handle("undo").startswith("Undid the coding merge"))

    def test_old_state_falls_back_to_fixed_order(self):
        s = {"files": 1, "memory": 1, "code": 0}
        mk = lambda n, l: UndoAgent(l, lambda: s[n], lambda: s[n] > 0, lambda: "ok")
        c = UndoCoordinator({"memory": mk("memory", "m"), "files": mk("files", "f"), "code": mk("code", "c")})
        self.assertTrue(c.handle("undo").startswith("Undid f"))

    def test_other_phrases_are_not_bare_undo(self):
        s, c = make(); s["files"] = 1; c.observe(1)
        for t in ("undo the merge", "bring that back", "what can I undo", "undo everything in downloads"): self.assertIsNone(c.handle(t), t)

    def test_declining_agent_is_skipped(self):
        c = UndoCoordinator({"files": UndoAgent("f", lambda: 1, lambda: True, lambda: None), "memory": UndoAgent("m", lambda: 1, lambda: True, lambda: "done")})
        self.assertEqual(c.handle("undo"), "Undid m. done")

    def test_candidate_wires_coordinator(self):
        src = open(REPO_ROOT / "julius/runtime.py").read()
        self.assertIn("_undo_coordinator.handle", src); self.assertIn("_undo_coordinator.observe", src)


if __name__ == "__main__":
    unittest.main()
