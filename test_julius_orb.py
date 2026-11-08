import plistlib, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace

import julius_orb as orb


class Recorder:
    def __init__(self, rc=0): self.calls = []; self.rc = rc
    def __call__(self, args, **kw): self.calls.append(args); return SimpleNamespace(returncode=self.rc, stdout="", stderr="")


class LoginItem(unittest.TestCase):
    def test_plist_starts_at_login_in_the_gui_session_and_restarts_after_a_crash_only(self):
        d = orb.plist_dict()
        self.assertEqual(d["Label"], "com.julius.orb"); self.assertTrue(d["RunAtLoad"]); self.assertEqual(d["KeepAlive"], {"SuccessfulExit": False}); self.assertEqual(d["LimitLoadToSessionType"], "Aqua")
        self.assertEqual(d["ProgramArguments"][1:], ["--feed", "ws://127.0.0.1:8765"]); self.assertNotIn("EnvironmentVariables", d, "no keys or secrets anywhere near the UI")

    def test_on_installs_and_bootstraps_without_touching_the_service(self):
        tmp = Path(tempfile.mkdtemp()); binary = tmp / "JuliusOrb"; binary.write_text("x"); rec = Recorder()
        ok, _ = orb.on(rec, tmp / "com.julius.orb.plist", binary)
        self.assertTrue(ok); self.assertEqual(plistlib.loads((tmp / "com.julius.orb.plist").read_bytes())["ProgramArguments"][0], str(binary))
        flat = " ".join(" ".join(c) for c in rec.calls); self.assertIn("bootstrap", flat); self.assertNotIn("com.julius.assistant", flat)

    def test_off_removes_only_its_own_plist(self):
        tmp = Path(tempfile.mkdtemp()); p = tmp / "com.julius.orb.plist"; p.write_text("x"); rec = Recorder()
        orb.off(rec, p); self.assertFalse(p.exists()); self.assertNotIn("com.julius.assistant", " ".join(" ".join(c) for c in rec.calls))

    def test_on_builds_first_when_the_app_is_missing_and_stops_if_the_build_fails(self):
        tmp = Path(tempfile.mkdtemp()); rec = Recorder(rc=1)
        ok, msg = orb.on(rec, tmp / "x.plist", tmp / "missing"); self.assertFalse(ok); self.assertIn("build failed", msg); self.assertFalse((tmp / "x.plist").exists())


if __name__ == "__main__":
    unittest.main()
