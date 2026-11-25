"""The test suite can never touch a running Julius: its ports are refused and its folders are throwaway (tests/__init__.py)."""

import os
import socket
import unittest
from pathlib import Path

import julius.memory.sessions as jm
import tests
from julius import state
from julius.conversations import store


class Isolation(unittest.TestCase):
    def test_the_service_ports_cannot_be_bound(self):
        for port in sorted(tests.SERVICE_PORTS):
            s = socket.socket()
            self.addCleanup(s.close)
            with self.subTest(port), self.assertRaisesRegex(OSError, "a running Julius uses it"):
                s.bind(("127.0.0.1", port))
        s = socket.socket()
        self.addCleanup(s.close)
        s.bind(("127.0.0.1", 0))  # an ephemeral port is fine
        self.assertNotIn(s.getsockname()[1], tests.SERVICE_PORTS)

    def test_state_memory_conversations_costs_and_local_config_are_throwaway(self):
        root = Path(tests.ROOT)
        for key in tests.ISOLATED:
            self.assertTrue(Path(os.environ[key]).is_relative_to(root), key)
        self.assertTrue(state.state_dir().is_relative_to(root))
        self.assertTrue(Path(jm.MEM).is_relative_to(root))
        self.assertTrue(store.default_path().is_relative_to(root))
        home = Path.home()
        for real in (home / ".julius", Path(__file__).resolve().parents[1] / "julius_memory"):
            self.assertFalse(state.state_dir().is_relative_to(real))
            self.assertFalse(Path(jm.MEM).is_relative_to(real))


if __name__ == "__main__":
    unittest.main()
