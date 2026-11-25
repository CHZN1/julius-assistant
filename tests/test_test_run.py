"""Scripted test turns get their own memory, conversation list and cost ledger; the real ones are never written."""

import unittest
from pathlib import Path

from julius.runtime import test_run


class TestRunIsolation(unittest.TestCase):
    def test_a_scripted_run_gets_a_fresh_folder_for_everything(self):
        env = {"JULIUS_VALIDATION_SCRIPT": "/tmp/s.json"}
        root = test_run.isolate(env)
        self.assertTrue(root.is_dir() and (root / "memory").is_dir())
        self.assertEqual(env["JULIUS_MEMORY_DIR"], str(root / "memory"))
        self.assertEqual(env["JULIUS_STATE_DIR"], str(root / "memory"))
        self.assertEqual(env["JULIUS_CONVERSATIONS_DB"], str(root / "conversations.sqlite3"))
        self.assertEqual(env["JULIUS_COSTS_FILE"], str(root / "memory" / "costs.jsonl"))
        for key in test_run.PATHS:
            self.assertFalse(env[key].startswith(str(Path.home() / ".julius")))
            self.assertNotIn("julius_memory", env[key])
        self.assertIsNone(test_run.isolate(env))  # the second call (boot) keeps the first folder
        self.assertEqual(env["JULIUS_MEMORY_DIR"], str(root / "memory"))

    def test_paths_the_caller_set_are_kept(self):
        env = {"JULIUS_VALIDATION_SCRIPT": "s.json", "JULIUS_MEMORY_DIR": "/work/mem"}
        root = test_run.isolate(env)
        self.assertEqual(env["JULIUS_MEMORY_DIR"], "/work/mem")
        self.assertEqual(env["JULIUS_CONVERSATIONS_DB"], str(root / "conversations.sqlite3"))

    def test_a_normal_run_and_the_opt_out_are_left_alone(self):
        for env in ({}, {"JULIUS_VALIDATION_SCRIPT": "s.json", "JULIUS_TEST_KEEP_MEMORY": "1"}):
            before = dict(env)
            self.assertIsNone(test_run.isolate(env))
            self.assertEqual(env, before)


if __name__ == "__main__":
    unittest.main()
