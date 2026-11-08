import json, os, subprocess, time, unittest
from pathlib import Path

import julius_events as ev

APP = Path(__file__).resolve().parent / "ui" / "app" / "build" / "Julius Orb.app" / "Contents" / "MacOS" / "JuliusOrb"
SRC = (Path(__file__).resolve().parent / "ui" / "app" / "main.swift").read_text()


def run_app(feed, seconds=5):
    p = subprocess.run([str(APP), "--feed", feed, "--no-hotkey", "--selftest", str(seconds)], capture_output=True, text=True, timeout=60)
    return json.loads(p.stdout.strip().splitlines()[-1])


class Source(unittest.TestCase):
    def test_window_hotkey_and_bridge_are_as_approved(self):
        for needle in ("level = .floating", "isOpaque = false", "kVK_ANSI_O", "controlKey | optionKey", "modifierFlags.contains(.option)", "performDrag", "setPaused", "setPower", "setOffline",
                       "127.0.0.1:8765", "var size: CGFloat = 420", "screen.maxX - args.size - 24", "Battery Power", "isLowPowerModeEnabled", "orb-settings.json", "orb-window.json"):
            self.assertIn(needle, SRC)


@unittest.skipUnless(APP.exists() and os.environ.get("JULIUS_UI_APP_TESTS", "1") == "1", "build the app first: ui/app/build.sh")
class AppEndToEnd(unittest.TestCase):
    def test_app_follows_the_feed_and_reports_offline_without_it(self):
        hub = ev.EventHub(); srv = ev.EventServer(hub, port=0).start()
        try:
            hub.set_state("thinking"); hub.say("you", "what is the weather", 1)
            r = run_app(f"ws://127.0.0.1:{srv.port}")
            self.assertTrue(r["feedConnected"]); self.assertEqual(r["page"]["mode"], "thinking")
            self.assertEqual(r["window"]["w"], 420); self.assertFalse(r["window"]["opaque"]); self.assertEqual(r["window"]["level"], 3)
            self.assertEqual(r["page"]["settings"]["finish"], "opal")
        finally: srv.stop()
        r = run_app("ws://127.0.0.1:1")   # nothing listening
        self.assertFalse(r["feedConnected"]); self.assertEqual(r["page"]["mode"], "offline")

    def test_quiet_and_asleep_reach_the_page(self):
        hub = ev.EventHub(); srv = ev.EventServer(hub, port=0).start()
        try:
            for st in ("quiet", "asleep"):
                hub.state = "idle"; hub.set_state(st); self.assertEqual(run_app(f"ws://127.0.0.1:{srv.port}", 4)["page"]["mode"], st)
        finally: srv.stop()


if __name__ == "__main__":
    unittest.main()
