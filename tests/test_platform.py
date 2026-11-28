"""The platform layer (julius/platforms): the Mac implementation builds exactly the commands Julius always ran, other
platforms import cleanly and say what's missing, and no Mac-only command is built outside the layer."""

import ast
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from julius import platforms
from julius.platforms import base, mac

ROOT = Path(__file__).resolve().parent.parent
DOM = f"gui/{os.getuid()}"


class MacCommands(unittest.TestCase):
    """Each expected list is copied from the caller's code before the move (2026-11-28)."""

    def setUp(self):
        self.m = mac.Mac()

    def test_launchd(self):
        s = self.m.services
        self.assertEqual(s.domain(), DOM)
        self.assertEqual(s.load(Path("/x/a.plist")), ["launchctl", "bootstrap", DOM, "/x/a.plist"])
        self.assertEqual(s.unload("L"), ["launchctl", "bootout", f"{DOM}/L"])
        self.assertEqual(s.enable("L"), ["launchctl", "enable", f"{DOM}/L"])
        self.assertEqual(s.disable("L"), ["launchctl", "disable", f"{DOM}/L"])
        self.assertEqual(s.describe("L"), ["launchctl", "print", f"{DOM}/L"])
        self.assertEqual(s.signal("L", "SIGUSR1"), ["launchctl", "kill", "SIGUSR1", f"{DOM}/L"])
        self.assertEqual(s.restart("L"), ["launchctl", "kickstart", "-k", f"{DOM}/L"])

    def test_hotkey_helper_build(self):
        self.assertEqual(
            self.m.helpers.compile(Path("/s/hotkeyd.c"), Path("/b/hk"), ("Carbon",)),
            ["/usr/bin/clang", "-O2", "/s/hotkeyd.c", "-framework", "Carbon", "-o", "/b/hk"],
        )

    def test_vision_helper_build(self):
        fw = (
            "AVFoundation",
            "CoreImage",
            "CoreMedia",
            "CoreVideo",
            "Foundation",
            "CoreGraphics",
            "Carbon",
            "Vision",
        )
        want = ["/usr/bin/clang", "-O2", "-fobjc-arc", "/s/visiond.m"]
        for f in fw:
            want += ["-framework", f]
        want += ["-sectcreate", "__TEXT", "__info_plist", "/s/Info.plist", "-o", "/b/vd"]
        got = self.m.helpers.compile(
            Path("/s/visiond.m"), Path("/b/vd"), fw, objc=True, info_plist=Path("/s/Info.plist")
        )
        self.assertEqual(got, want)

    def test_automation(self):
        self.assertEqual(
            self.m.automation.run_script(Path("/r/native/apple/bridge.js")),
            ["/usr/bin/osascript", "-l", "JavaScript", "/r/native/apple/bridge.js"],
        )

    def test_afconvert(self):
        a = self.m.audio
        self.assertEqual(a.name, "afconvert")
        # music/convert.py: wav 24-bit at 48 kHz, aiff 16-bit at the source rate
        self.assertEqual(
            a.pcm("afconvert", "/in.flac", "/out.wav", "wav", 24, 48000),
            ["afconvert", "-f", "WAVE", "-d", "LEI24@48000", "/in.flac", "/out.wav"],
        )
        self.assertEqual(
            a.pcm("afconvert", "/in.flac", "/out.aiff", "aiff", 16),
            ["afconvert", "-f", "AIFF", "-d", "BEI16", "/in.flac", "/out.aiff"],
        )
        # music/analysis.py: mono 16-bit at 22.05 kHz, by full path
        self.assertEqual(
            a.pcm("/usr/bin/afconvert", "/a.m4a", "/t.wav", "wav", 16, 22050, channels=1),
            [
                "/usr/bin/afconvert",
                "-f",
                "WAVE",
                "-d",
                "LEI16@22050",
                "-c",
                "1",
                "/a.m4a",
                "/t.wav",
            ],
        )
        with (
            mock.patch("shutil.which", return_value=None),
            mock.patch.object(Path, "exists", return_value=True),
        ):
            self.assertFalse(a.available())
            self.assertEqual(a.path(), "/usr/bin/afconvert")

    def test_speech_worker(self):
        self.assertEqual(self.m.speech.tts_worker(True), "julius.audio.mlx_tts_worker")
        self.assertEqual(self.m.speech.tts_worker(False), "julius.audio.tts_worker")


class OtherPlatforms(unittest.TestCase):
    def test_this_mac_gets_the_mac_implementation(self):
        if sys.platform == "darwin":
            self.assertIsInstance(platforms.current(), mac.Mac)

    def test_elsewhere_imports_work_and_features_say_what_is_missing(self):
        p = base.Unsupported("linux")
        self.assertFalse(p.audio.available())
        self.assertIsNone(p.audio.path())
        with self.assertRaises(platforms.PlatformUnsupported) as e:
            p.services.restart("com.julius.assistant")
        self.assertIn("Login services", str(e.exception))
        self.assertIn("linux", str(e.exception))


# Exact command strings that only the platform layer may write. A caller that needs one asks platforms.current().
MAC_ONLY = {"launchctl", "afconvert", "/usr/bin/osascript", "/usr/bin/clang", "-framework"}
LAYER = ROOT / "julius" / "platforms"


class Boundary(unittest.TestCase):
    def test_no_mac_only_command_is_built_outside_the_layer(self):
        found = []
        for path in sorted((ROOT / "julius").rglob("*.py")):
            if LAYER in path.parents:
                continue
            for node in ast.walk(ast.parse(path.read_text(), str(path))):
                if isinstance(node, ast.Constant) and node.value in MAC_ONLY:
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.value!r}")
        self.assertEqual(found, [], "build these through julius.platforms.current()")


if __name__ == "__main__":
    unittest.main()
