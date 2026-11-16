"""Unit tests for undo."""

import unittest

from julius.brain.undo import NOTHING, UndoAgent, UndoCoordinator


def make():
    s = {"files": 0, "memory": 0, "code": 0, "done": []}

    def agent(name, label, msg):
        return UndoAgent(
            label,
            lambda: s[name],
            lambda: s[name] > 0,
            lambda: (s.__setitem__(name, 0), s["done"].append(name), msg)[2],
        )

    c = UndoCoordinator(
        {
            "files": agent("files", "the last file change", "Moved 2 files back."),
            "code": agent("code", "the coding merge", "Restored 3 files."),
            "memory": agent("memory", "your last forget", "I've put it back."),
        }
    )
    return s, c


class UndoTests(unittest.TestCase):
    def test_most_recent_agent_wins_and_is_named(self):
        s, c = make()
        s["memory"] = 1
        c.observe(1)
        s["files"] = 1
        c.observe(2)
        self.assertEqual(c.handle("undo"), "Undid the last file change. Moved 2 files back.")
        self.assertEqual(
            c.handle("undo that"),
            "Undid your last forget. I've put it back.",
            "repeats down the stack",
        )
        self.assertEqual(c.handle("undo"), NOTHING, "nothing left from this session")

    def test_memory_after_files(self):
        s, c = make()
        s["files"] = 1
        c.observe(1)
        s["memory"] = 1
        c.observe(2)
        self.assertTrue(c.handle("please undo it").startswith("Undid your last forget"))

    def test_coding_merge_most_recent(self):
        s, c = make()
        s["files"] = 1
        c.observe(1)
        s["code"] = 1
        c.observe(2)
        self.assertTrue(c.handle("undo").startswith("Undid the coding merge"))

    def test_a_bare_undo_never_reaches_back_before_this_session(self):
        """The live bug: after sprite edits, "Undo." undid a month-old file move. Old actions need the agent named."""
        s = {"files": 1, "memory": 1, "code": 0}
        ran = []

        def mk(n, line):
            return UndoAgent(line, lambda: s[n], lambda: s[n] > 0, lambda: ran.append(n) or "ok")

        c = UndoCoordinator(
            {
                "memory": mk("memory", "m"),
                "files": mk("files", "the last file change"),
                "code": mk("code", "c"),
            }
        )
        self.assertEqual(c.handle("undo"), NOTHING)
        self.assertEqual(ran, [])
        self.assertEqual(c.handle("undo the file change"), "Undid the last file change. ok")
        self.assertEqual(ran, ["files"])

    def test_after_sprite_edits_undo_is_the_sprite_and_files_stay_put(self):
        s = {"files": 1, "sprite": 0}  # a file batch from before this run, then sprite edits now
        ran = []

        def mk(n, line):
            return UndoAgent(
                line,
                lambda: s[n],
                lambda: s[n] > 0,
                lambda: (ran.append(n), s.__setitem__(n, s[n] - 1), "ok")[2],
            )

        c = UndoCoordinator(
            {
                "files": mk("files", "the last file change"),
                "sprite": mk("sprite", "the last sprite edit"),
            }
        )
        s["sprite"] = 2
        c.observe(5)
        self.assertEqual(c.handle("Undo."), "Undid the last sprite edit. ok")
        self.assertEqual(c.handle("undo"), "Undid the last sprite edit. ok")
        self.assertEqual(c.handle("undo"), NOTHING)
        self.assertEqual(ran, ["sprite", "sprite"])  # never the file move
        self.assertEqual(c.handle("undo the sprite edit"), "There's no sprite edit to undo.")

    def test_other_phrases_are_not_bare_undo(self):
        s, c = make()
        s["files"] = 1
        c.observe(1)
        for t in (
            "undo the merge",
            "bring that back",
            "what can I undo",
            "undo everything in downloads",
        ):
            self.assertIsNone(c.handle(t), t)

    def test_declining_agent_is_skipped(self):
        mark = {"v": 0}
        c = UndoCoordinator(
            {
                "files": UndoAgent("f", lambda: mark["v"], lambda: True, lambda: None),
                "memory": UndoAgent("m", lambda: mark["v"], lambda: True, lambda: "done"),
            }
        )
        mark["v"] = 1
        c.observe(1)
        self.assertEqual(c.handle("undo"), "Undid m. done")

    def test_candidate_wires_coordinator(self):
        from unittest.mock import Mock

        from tests.harness import ProcessHarness

        h = ProcessHarness()
        h.session.undo_coordinator = Mock()
        h.session.undo_coordinator.handle.return_value = "Undid the last file change."
        h.process("undo")
        h.session.undo_coordinator.observe.assert_called_once_with(1)
        h.session.undo_coordinator.handle.assert_called_once_with("undo")
        self.assertEqual(h["turn.speak"].call_args.args[1], "Undid the last file change.")


if __name__ == "__main__":
    unittest.main()
