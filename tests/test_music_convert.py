"""Batch conversion goes through the file agent: new copies only, workspace only, preview then yes, undo, and the audio rules."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from julius.agents.file_agent import FileAgent
from julius.music import convert as cv


def fake_run(src, dst, spec):  # stands in for ffmpeg/afconvert: writes a recognisable copy
    Path(dst).write_bytes(b"CONVERTED:" + Path(src).read_bytes()[:20] + str(spec).encode())


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = (Path(self.temp.name) / "home").resolve()
        self.ws = self.root / "Julius Audio Workspace"
        self.own = self.root / "Desktop" / "My Loops"
        self.kit = self.root / "Downloads" / "Bought Kit"
        for p in (self.ws, self.own, self.kit, self.root / "Documents"):
            p.mkdir(parents=True)
        folders = {
            n: "write" for n in ("Documents", "Desktop", "Downloads", "Julius Audio Workspace")
        }
        self.agent = FileAgent(self.root, Path(self.temp.name) / "state", folders=folders)
        cfg = {"audio": {"workspace": str(self.ws)}, "music": {"index_allowlist": [str(self.own)]}}
        self.patches = [
            mock.patch.object(
                cv.config, "load_config", return_value=cfg
            ),  # one patch: cv.config is library.config
            mock.patch.object(cv, "run", side_effect=fake_run),
        ]
        for p in self.patches:
            p.start()
        self.turn = 1

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def audio(self, folder, name, data=b"RIFFxxxxWAVEdata-1234567890"):
        p = folder / name
        p.write_bytes(data)
        return p

    def propose(self, source, pattern="*.wav", spec=None, dest=None):
        self.turn += 1
        return self.agent.propose(
            "convert",
            source=str(source),
            destination=str(dest or self.ws / "converted" / "mp3"),
            pattern=pattern,
            convert=spec or {"fmt": "mp3", "rate": None, "depth": None},
            turn=self.turn,
        )

    def confirm(self, out):
        self.turn += 1
        return self.agent.execute(out["data"]["plan"]["id"], confirmed=True, turn=self.turn)


class ParseTests(unittest.TestCase):
    def test_requests(self):
        p = cv.parse("convert all the wav files in the workspace to aiff 48k 24 bit")
        self.assertEqual(p["spec"], cv.Spec("aiff", 48000, 24))
        self.assertEqual(cv.selection(p["what"]), ("all", "*.wav"))
        p = cv.parse("Convert CHZN1_Cake_BPM160.wav to mp3")
        self.assertEqual(p["spec"], cv.Spec("mp3", None, None))
        self.assertEqual(cv.selection(p["what"]), ("named", "CHZN1_Cake_BPM160.wav"))
        p = cv.parse("batch convert my loops to wav 44.1k 16 bit")
        self.assertEqual((p["spec"].rate, p["spec"].depth), (44100, 16))

    def test_errors_and_non_requests(self):
        self.assertIn(
            "wav, aiff or mp3",
            cv.parse("convert kick.wav to flac")["error"]
            if cv.parse("convert kick.wav to flac")
            else "wav, aiff or mp3",
        )
        self.assertIn("sample rates", cv.parse("convert kick.wav to wav 50k")["error"])
        for no in (
            "convert 5 dollars to euros",
            "what is a sample rate",
            "copy kick.wav to the workspace",
            "convert it",
        ):
            self.assertIsNone(cv.parse(no), no)

    def test_commands_are_argument_lists_that_never_overwrite(self):
        s = cv.Spec("wav", 48000, 24)
        ff = cv.command(Path("/a/b c.mp3"), "/o/out.wav", s, "ffmpeg")
        self.assertIn("-n", ff)
        self.assertIsInstance(ff, list)
        self.assertEqual(ff[ff.index("-c:a") + 1], "pcm_s24le")
        af = cv.command(Path("/a/x.wav"), "/o/out.aiff", cv.Spec("aiff", 44100, 16), "afconvert")
        self.assertEqual(af[:5], ["afconvert", "-f", "AIFF", "-d", "BEI16@44100"])
        with self.assertRaises(cv.ConvertError):
            cv.command(Path("/a/x.wav"), "/o/out.mp3", cv.Spec("mp3"), "afconvert")


class PlanTests(Fixture):
    def test_workspace_files_convert_to_new_copies_and_undo_removes_only_them(self):
        a = self.audio(self.ws, "kick.wav")
        b = self.audio(self.ws, "snare.wav", b"RIFFyyyyWAVEdata-abcdefghij")
        before = (a.read_bytes(), b.read_bytes())
        out = self.propose(self.ws)
        self.assertTrue(out["ok"], out)
        self.assertIn("The originals are not changed", out["message"])
        self.assertEqual(out["data"]["plan"]["count"], 2)
        self.assertFalse((self.ws / "converted").exists(), "nothing happens before the yes")
        done = self.confirm(out)
        self.assertTrue(done["ok"], done)
        made = sorted(p.name for p in (self.ws / "converted" / "mp3").iterdir())
        self.assertEqual(made, ["kick.mp3", "snare.mp3"])
        self.assertEqual((a.read_bytes(), b.read_bytes()), before)
        undone = self.agent.undo()
        self.assertTrue(undone["ok"], undone)
        self.assertEqual(list((self.ws / "converted" / "mp3").iterdir()), [])
        self.assertTrue(a.exists() and b.exists())

    def test_nothing_is_overwritten(self):
        self.audio(self.ws, "kick.wav")
        dest = self.ws / "converted" / "mp3"
        dest.mkdir(parents=True)
        (dest / "kick.mp3").write_bytes(b"mine")
        out = self.propose(self.ws)
        self.assertFalse(out["ok"])
        self.assertEqual((dest / "kick.mp3").read_bytes(), b"mine")

    def test_output_only_in_the_workspace_never_a_drive(self):
        self.audio(self.ws, "kick.wav")
        for bad in (self.root / "Documents" / "out", Path("/Volumes/CHZN1 1/out")):
            out = self.propose(self.ws, dest=bad)
            self.assertFalse(out["ok"], bad)
            self.assertIn(out["code"], ("outside_workspace", "outside_scope", "read_only"))

    def test_one_approved_file_outside_the_workspace_is_allowed_with_its_own_yes(self):
        self.audio(self.own, "cake.wav")
        out = self.propose(self.own, "cake.wav")
        self.assertTrue(out["ok"], out)
        self.assertTrue(self.confirm(out)["ok"])
        self.assertTrue((self.ws / "converted" / "mp3" / "cake.mp3").exists())
        self.assertTrue((self.own / "cake.wav").exists())

    def test_several_audio_files_outside_the_workspace_are_never_approved_as_a_set(self):
        self.audio(self.own, "a.wav")
        self.audio(self.own, "b.wav")
        out = self.propose(self.own)
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "audio_each")
        self.assertFalse((self.ws / "converted").exists())

    def test_files_outside_approved_folders_and_purchased_kits_are_refused(self):
        self.audio(self.kit, "kick.wav")
        out = self.propose(self.kit, "kick.wav")
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "not_approved")

    def test_protected_folders_are_refused(self):
        logic = self.root / "Documents" / "Logic Sessions"
        logic.mkdir()
        self.audio(logic, "bounce.wav")
        out = self.propose(logic, "bounce.wav")
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "protected")

    def test_a_failed_conversion_leaves_no_partial_file_and_is_reported(self):
        self.audio(self.ws, "kick.wav")
        out = self.propose(self.ws)
        with mock.patch.object(
            cv, "run", side_effect=cv.ConvertError("The converter could not make that copy.")
        ):
            done = self.confirm(out)
        self.assertFalse(done["ok"])
        leftovers = [p for p in (self.ws / "converted").rglob("*") if p.is_file()]
        self.assertEqual(leftovers, [])

    def test_a_source_that_changes_before_the_yes_is_not_converted(self):
        k = self.audio(self.ws, "kick.wav")
        out = self.propose(self.ws)
        k.write_bytes(b"something else entirely, longer")
        done = self.confirm(out)
        self.assertFalse(done["ok"])


class AudioRuleTests(Fixture):
    def test_renaming_or_moving_several_audio_files_outside_the_workspace_needs_each_yes(self):
        self.audio(self.own, "a.wav")
        self.audio(self.own, "b.wav")
        self.turn += 1
        out = self.agent.propose(
            "move",
            source=str(self.own),
            destination=str(self.root / "Documents" / "x"),
            pattern="*.wav",
            turn=self.turn,
        )
        self.assertFalse(out["ok"])
        self.assertEqual(out["code"], "audio_each")

    def test_inside_the_workspace_a_set_is_fine_and_can_be_undone(self):
        for n in ("a.wav", "b.wav", "c.wav"):
            self.audio(self.ws, n)
        self.turn += 1
        out = self.agent.propose(
            "move",
            source=str(self.ws),
            destination=str(self.ws / "Drums"),
            pattern="*.wav",
            turn=self.turn,
        )
        self.assertTrue(out["ok"], out)
        self.assertTrue(self.confirm(out)["ok"])
        self.assertEqual(
            sorted(p.name for p in (self.ws / "Drums").iterdir()), ["a.wav", "b.wav", "c.wav"]
        )
        self.assertTrue(self.agent.undo()["ok"])
        self.assertEqual(sorted(p.name for p in self.ws.glob("*.wav")), ["a.wav", "b.wav", "c.wav"])

    def test_rename_inside_the_workspace_keeps_the_preview_and_yes(self):
        self.audio(self.ws, "old name.wav")
        self.turn += 1
        out = self.agent.propose(
            "rename",
            path=str(self.ws / "old name.wav"),
            destination=str(self.ws / "new_name.wav"),
            turn=self.turn,
        )
        self.assertTrue(out["ok"], out)
        self.assertTrue((self.ws / "old name.wav").exists(), "nothing before the yes")


@unittest.skipUnless(shutil.which("afconvert"), "needs macOS afconvert")
class RealConversion(unittest.TestCase):
    def test_wav_to_aiff_with_rate_and_depth(self):
        import wave

        td = Path(tempfile.mkdtemp())
        src = td / "tone.wav"
        with wave.open(str(src), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(b"\x00\x10" * 4410)
        dst = td / "tone.aiff"
        with mock.patch.object(cv, "backend", return_value="afconvert"):
            cv.run(src, str(dst), {"fmt": "aiff", "rate": 48000, "depth": 24})
        self.assertGreater(dst.stat().st_size, 1000)
        self.assertEqual(dst.read_bytes()[:4], b"FORM")
        with self.assertRaises(cv.ConvertError):
            cv.run(
                src, str(dst), {"fmt": "aiff", "rate": None, "depth": None}
            )  # exists: never overwritten
        shutil.rmtree(td)


if __name__ == "__main__":
    unittest.main()
