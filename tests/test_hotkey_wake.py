"""The wake hotkey and the wake signal bring him back from sleep and from quiet mode."""

import inspect
import time
import unittest
from unittest.mock import patch

from julius.audio.listening import listening_control
from julius.runtime import boot, controls
from tests.harness import make_session


class HotkeyWake(unittest.TestCase):
    def wake(self, awake_until: float, source: str = "hotkey"):
        s = make_session(awake_until=awake_until)
        with patch.object(controls, "leave_quiet") as leave, patch("builtins.print"):
            controls.hotkey_wake(s, source)
        return s, leave

    def test_wakes_from_ordinary_sleep(self):
        s, leave = self.wake(0.0)
        self.assertEqual(list(s.typed), ["wake up"])
        leave.assert_called_once_with(s, "hotkey")

    def test_wakes_from_quiet_mode_too(self):
        s, _ = self.wake(0.0, "signal")
        self.assertEqual(list(s.typed), ["wake up"])

    def test_does_nothing_extra_when_already_awake(self):
        s, _ = self.wake(time.time() + 60)
        self.assertEqual(list(s.typed), [])

    def test_hotkey_and_signal_use_it_and_the_wake_command_is_recognised(self):
        source = inspect.getsource(boot.setup_wake_hotkey)
        self.assertIn('controls.hotkey_wake(s, "hotkey")', source)
        self.assertIn('controls.hotkey_wake(s, "signal")', source)
        self.assertEqual(listening_control("wake up"), "wake")


if __name__ == "__main__":
    unittest.main()
