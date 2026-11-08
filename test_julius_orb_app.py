import json, os, subprocess, time, unittest
from pathlib import Path

import julius_events as ev

APP = Path(__file__).resolve().parent / "ui" / "app" / "build" / "Julius Orb.app" / "Contents" / "MacOS" / "JuliusOrb"
SRC = (Path(__file__).resolve().parent / "ui" / "app" / "main.swift").read_text()


def run_app(feed, seconds=5, support=None, eval_js=None):
    import tempfile
    env = dict(os.environ, JULIUS_ORB_SUPPORT=str(support or tempfile.mkdtemp()))   # never touch the real settings file
    p = subprocess.run([str(APP), "--feed", feed, "--no-hotkey", "--selftest", str(seconds), *(["--eval", eval_js] if eval_js else [])], capture_output=True, text=True, timeout=60, env=env)
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



@unittest.skipUnless(APP.exists(), "build the app first: ui/app/build.sh")
class SettingsAndOffline(unittest.TestCase):
    def test_settings_are_saved_and_restored(self):
        import tempfile
        d = Path(tempfile.mkdtemp())
        run_app("ws://127.0.0.1:1", 3, d, "document.querySelector('#finishes button[data-v=\"cobalt\"]').click(); document.querySelector('#power button[data-v=\"low\"]').click(); const e=document.getElementById('swirl'); e.value=35; e.dispatchEvent(new Event('input'));")
        saved = json.loads((d / "orb-settings.json").read_text())
        self.assertEqual((saved["finish"], saved["power"], saved["swirl"]), ("cobalt", "low", 35))
        r = run_app("ws://127.0.0.1:1", 3, d)["page"]["settings"]
        self.assertEqual((r["finish"], r["power"], r["swirl"]), ("cobalt", "low", 35))
        self.assertEqual(r["silk"], 80, "untouched settings keep the approved defaults")

    def test_all_eight_finishes_selectable(self):
        import tempfile
        for key in ("gold", "rose", "silver", "emerald", "amethyst", "opal", "obsidian", "cobalt"):
            d = Path(tempfile.mkdtemp())
            run_app("ws://127.0.0.1:1", 2, d, f"document.querySelector('#finishes button[data-v=\"{key}\"]').click();")
            self.assertEqual(json.loads((d / "orb-settings.json").read_text())["finish"], key)

    def test_low_power_cap_applies(self):
        import tempfile
        d = Path(tempfile.mkdtemp()); (d / "orb-settings.json").write_text(json.dumps({"power": "low", "count": 2400}))
        self.assertEqual(run_app("ws://127.0.0.1:1", 3, d)["page"]["grains"], 800000)

    def test_offline_then_recovers_when_the_feed_appears(self):
        import socket, threading
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        hub = ev.EventHub(); box = {}
        def later():
            time.sleep(3.0); box["srv"] = ev.EventServer(hub, port=port).start(); hub.set_state("speaking")
        t = threading.Thread(target=later); t.start()
        try: r = run_app(f"ws://127.0.0.1:{port}", 9)
        finally:
            t.join()
            if "srv" in box: box["srv"].stop()
        self.assertTrue(r["feedConnected"]); self.assertEqual(r["page"]["mode"], "speaking")


if __name__ == "__main__":
    unittest.main()
