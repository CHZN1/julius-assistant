import ast, collections, time, unittest
from pathlib import Path

SRC = Path(__file__).with_name("julius_candidate.py").read_text()


def load(**g):
    tree = ast.parse(SRC); fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "hotkey_wake")
    ns = {"time": time, "print": lambda *a, **k: None, **g}; exec(compile(ast.Module([fn], []), "x", "exec"), ns); return ns


class HotkeyWake(unittest.TestCase):
    def test_wakes_from_ordinary_sleep(self):
        typed = collections.deque(); calls = []
        ns = load(leave_quiet=lambda s: calls.append(s), typed=typed, awake_until=0.0)
        ns["hotkey_wake"]("hotkey"); self.assertEqual(list(typed), ["wake up"]); self.assertEqual(calls, ["hotkey"])

    def test_wakes_from_quiet_mode_too(self):
        typed = collections.deque(); ns = load(leave_quiet=lambda s: None, typed=typed, awake_until=0.0); ns["hotkey_wake"]("signal"); self.assertEqual(list(typed), ["wake up"])

    def test_does_nothing_extra_when_already_awake(self):
        typed = collections.deque(); ns = load(leave_quiet=lambda s: None, typed=typed, awake_until=time.time() + 60); ns["hotkey_wake"](); self.assertEqual(list(typed), [])

    def test_a_press_before_the_queue_exists_does_not_crash(self):
        ns = load(leave_quiet=lambda s: None); ns["hotkey_wake"]()

    def test_hotkey_and_signal_use_it_and_the_wake_command_is_recognised(self):
        self.assertIn("julius_hotkey.start(lambda:hotkey_wake('hotkey'),QUIET_HOTKEY)", SRC); self.assertIn("lambda *a:hotkey_wake('signal')", SRC)
        from julius_listening import listening_control
        self.assertEqual(listening_control("wake up"), "wake")


if __name__ == "__main__":
    unittest.main()
