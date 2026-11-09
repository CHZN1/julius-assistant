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
        for needle in ("level = .floating", "isOpaque = false", "kVK_ANSI_O", "controlKey | optionKey", "modifierFlags.contains(.option)", "setPaused", "setPower", "setOffline",
                       "127.0.0.1:8765", "var size: CGFloat = 420", "let tall: CGFloat = 1.24", "screen.maxX - args.size - 24", "Battery Power", "isLowPowerModeEnabled", "orb-settings.json", "orb-window.json"):
            self.assertIn(needle, SRC)


@unittest.skipUnless(APP.exists() and os.environ.get("JULIUS_UI_APP_TESTS", "1") == "1", "build the app first: ui/app/build.sh")
class AppEndToEnd(unittest.TestCase):
    def test_app_follows_the_feed_and_reports_offline_without_it(self):
        hub = ev.EventHub(); srv = ev.EventServer(hub, port=0).start()
        try:
            hub.set_state("thinking"); hub.say("you", "what is the weather", 1)
            r = run_app(f"ws://127.0.0.1:{srv.port}")
            self.assertTrue(r["feedConnected"]); self.assertEqual(r["page"]["mode"], "thinking")
            self.assertEqual((r["window"]["w"], r["window"]["h"]), (420, 521)); self.assertFalse(r["window"]["opaque"]); self.assertEqual(r["window"]["level"], 3)
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



def run_app2(support, extra=(), seconds=3, feed="ws://127.0.0.1:1"):
    env = dict(os.environ, JULIUS_ORB_SUPPORT=str(support))
    p = subprocess.run([str(APP), "--feed", feed, "--no-hotkey", "--selftest", str(seconds), *extra], capture_output=True, text=True, timeout=60, env=env)
    return json.loads(p.stdout.strip().splitlines()[-1])


@unittest.skipUnless(APP.exists(), "build the app first: ui/app/build.sh")
class DrawerAndPlacement(unittest.TestCase):
    def drawer(self, js):
        import tempfile
        return run_app2(tempfile.mkdtemp(), ["--eval", js])["page"]["drawerOpen"]

    def test_drawer_closes_with_the_x_escape_and_click_outside(self):
        self.assertTrue(self.drawer("julius.toggleSettings();"))
        self.assertFalse(self.drawer("julius.toggleSettings(); document.getElementById('closeDrawer').click();"))
        self.assertFalse(self.drawer("julius.toggleSettings(); document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));"))
        self.assertFalse(self.drawer("julius.toggleSettings(); document.getElementById('orb').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));"))
        self.assertTrue(self.drawer("julius.toggleSettings(); document.getElementById('drawer').dispatchEvent(new PointerEvent('pointerdown',{bubbles:true}));"), "a click inside the drawer keeps it open")

    def test_window_stays_exactly_where_it_is_dropped_and_is_remembered(self):
        import tempfile
        d = Path(tempfile.mkdtemp()); (d / "orb-window.json").write_text(json.dumps({"x": 300.0, "y": 200.0, "w": 420.0}))
        r = run_app2(d, ["--move", "137,-41"])["window"]
        self.assertEqual((r["x"], r["y"], r["w"], r["h"]), (437.0, 159.0, 420.0, 521.0))
        saved = json.loads((d / "orb-window.json").read_text()); self.assertEqual((saved["x"], saved["y"], saved["w"]), (437.0, 159.0, 420.0))
        r2 = run_app2(d)["window"]; self.assertEqual((r2["x"], r2["y"], r2["w"]), (437.0, 159.0, 420.0), "restored exactly on the next launch")

    def test_no_snapping_to_screen_halves_at_the_edges(self):
        import tempfile
        for x, y in ((2.0, 120.0), (900.0, 40.0)):
            d = Path(tempfile.mkdtemp()); (d / "orb-window.json").write_text(json.dumps({"x": x, "y": y, "w": 420.0}))
            r = run_app2(d, ["--move", "0,0"])["window"]; self.assertEqual((r["x"], r["y"], r["w"]), (x, y, 420.0))

    def test_source_does_not_use_the_system_window_drag(self):
        self.assertNotIn("performDrag", SRC)


if __name__ == "__main__":
    unittest.main()
