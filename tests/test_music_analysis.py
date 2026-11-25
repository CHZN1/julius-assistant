"""Stage 5c-music item 1: BPM and key detection, the protected-folder rule, file lookup, the card and the tool wiring."""

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import julius.brain.tools as tools
import julius.ui_bridge.charts as charts
import julius.ui_bridge.events as ev
import julius.ui_bridge.panel as pn
import julius.ui_bridge.panelfiles as pf
from julius.brain.dispatcher import Dispatcher
from julius.music import analysis, policy, testsignals
from julius.music import tool as music_tool

CASES = [
    (70, "A minor"),
    (90, "C major"),
    (100, "F# minor"),
    (120, "D major"),
    (128, "E minor"),
    (140, "Bb major"),
    (150, "G minor"),
    (174, "C# minor"),
]


def octave_ok(found: float, truth: float) -> bool:
    return any(abs(found - truth * r) / (truth * r) <= 0.02 for r in (0.5, 1, 2))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def clip(self, name="clip.wav", bpm=120, key="D major", seconds=20.0, seed=1) -> Path:
        p = self.dir / name
        testsignals.write(p, bpm, key, seconds, seed)
        return p


class Policy(unittest.TestCase):
    def test_protected_music_locations_are_refused(self):
        home = os.path.expanduser("~")
        for p in (
            "/Library/Audio/Plug-Ins/x.wav",
            f"{home}/Music/Logic/Project.logicx/a.wav",
            f"{home}/Music/GarageBand/loop.wav",
            f"{home}/Library/Audio/Apple Loops/a.caf",
            f"{home}/Music/Waves/preset.wav",
            f"{home}/Music/Native Instruments/Kontakt/a.wav",
            f"{home}/Music/Samples/Slate/kick.wav",
            f"{home}/Music/Zebra/a.wav",
            f"{home}/Spitfire/a.wav",
            f"{home}/Music/Plug-Ins/x.wav",
            "/Applications/Logic Pro.app/a.wav",
        ):
            self.assertIsNotNone(policy.denied(p), p)

    def test_ordinary_folders_are_allowed(self):
        home = os.path.expanduser("~")
        for p in (
            f"{home}/Downloads/loop.wav",
            f"{home}/Music/Samples/kick.wav",
            f"{home}/Documents/niagara.wav",
        ):
            self.assertIsNone(policy.denied(p), p)

    def test_a_link_into_a_protected_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            real = Path(d) / "Logic"
            real.mkdir()
            (real / "a.wav").write_bytes(b"x")
            link = Path(d) / "innocent.wav"
            link.symlink_to(real / "a.wav")
            self.assertIsNotNone(policy.denied(link))


class Accuracy(Base):
    def test_labelled_synthetic_clips(self):
        key_ok = acc1 = acc2 = 0
        for i, (bpm, key) in enumerate(CASES):
            a = analysis.analyze(self.clip(f"c{i}.wav", bpm, key, 25.0, i))
            key_ok += a.key == key
            acc1 += abs(a.bpm - bpm) / bpm <= 0.02
            acc2 += octave_ok(a.bpm, bpm)
        self.assertGreaterEqual(key_ok, 7, f"keys {key_ok}/8")
        self.assertEqual(acc2, 8, f"tempo within an octave {acc2}/8")
        self.assertGreaterEqual(acc1, 6, f"tempo exact {acc1}/8")

    def test_octave_alternative_is_reported(self):
        a = analysis.analyze(self.clip("o.wav", 90, "C major", 25.0, 2))
        self.assertTrue(
            any(
                abs(b - f * a.bpm) / (f * a.bpm) < 0.03
                for b in a.bpm_alternatives
                for f in (0.5, 2)
            )
            or a.bpm_alternatives == []
        )

    def test_a_drum_only_loop_has_no_clear_key_and_a_melodic_one_does(self):
        import soundfile as sf

        drums = self.dir / "drums_BPM120.wav"
        sf.write(
            str(drums), testsignals.render(120, "A minor", 20.0, 4, tonal=False), testsignals.SR
        )
        a = analysis.analyze(drums)
        self.assertEqual((a.key, a.key_clear), ("", False))
        self.assertIn("No clear key", analysis.spoken(a))
        self.assertIn(
            "No clear key", [r for r in analysis.table_rows(a) if r[0] == "Key"][0][1].capitalize()
        )
        b = analysis.analyze(self.clip("melody.wav", 120, "A minor", 20.0, 4))
        self.assertTrue(b.key_clear and b.key)

    def test_filename_bpm_is_shown_beside_the_measured_one_not_trusted(self):
        a = analysis.analyze(self.clip("loop_BPM94.wav", 120, "D major", 20.0, 5))
        self.assertEqual(a.filename_bpm, 94)
        self.assertFalse(analysis.bpm_agrees(a))
        self.assertIn("file name says 94", analysis.spoken(a))
        self.assertIn(["Filename says", "94 BPM", "differs"], analysis.table_rows(a))
        c = analysis.analyze(self.clip("loop_bpm120.wav", 120, "D major", 20.0, 5))
        self.assertTrue(analysis.bpm_agrees(c))
        self.assertIn("matches the 120", analysis.spoken(c))
        d = analysis.analyze(self.clip("plain.wav", 120, "D major", 20.0, 5))
        self.assertIsNone(d.filename_bpm)
        self.assertNotIn("file name", analysis.spoken(d))

    def test_duration_and_confidence_fields(self):
        a = analysis.analyze(self.clip("d.wav", 120, "A minor", 20.0, 3))
        self.assertAlmostEqual(a.duration, 20.0, delta=0.1)
        self.assertTrue(0.0 <= a.bpm_confidence <= 1.0 and 0.0 <= a.key_confidence <= 1.0)
        self.assertEqual(analysis.clock(125.4), "2:05")

    def test_long_files_are_read_from_the_middle(self):
        p = self.clip("long.wav", 100, "G major", 130.0, 4)
        a = analysis.analyze(p)
        self.assertEqual(a.analysed_seconds, 90.0)
        self.assertIn("Analysed 90 seconds", a.note)
        self.assertAlmostEqual(a.duration, 130.0, delta=0.1)

    @unittest.skipUnless(
        shutil.which("afconvert") or Path("/usr/bin/afconvert").exists(), "needs macOS afconvert"
    )
    def test_formats_soundfile_cannot_read_go_through_afconvert(self):
        wav = self.clip("src.wav", 120, "D major", 15.0, 5)
        m4a = self.dir / "src.m4a"
        done = subprocess.run(
            ["/usr/bin/afconvert", "-f", "m4af", "-d", "aac", str(wav), str(m4a)],
            capture_output=True,
        )
        if done.returncode != 0:
            self.skipTest("afconvert could not make an m4a here")
        a = analysis.analyze(m4a)
        self.assertTrue(octave_ok(a.bpm, 120))
        self.assertEqual(a.key, "D major")
        self.assertEqual(
            [p.name for p in self.dir.iterdir() if p.suffix not in (".wav", ".m4a")], []
        )

    def test_analysis_is_read_only(self):
        p = self.clip("ro.wav", 110, "E minor", 15.0, 6)
        before = (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
            sorted(x.name for x in self.dir.iterdir()),
        )
        analysis.analyze(p)
        after = (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
            sorted(x.name for x in self.dir.iterdir()),
        )
        self.assertEqual(before, after)


class Refusals(Base):
    def test_problems_are_speakable(self):
        short = self.clip("short.wav", 120, "C major", 1.0, 1)
        for p, expect in (
            (self.dir / "missing.wav", "couldn't find"),
            (short, "too short"),
        ):
            with self.assertRaises(analysis.AudioProblem) as cm:
                analysis.analyze(p)
            self.assertIn(expect, str(cm.exception))
        txt = self.dir / "notes.txt"
        txt.write_text("hi")
        with self.assertRaises(analysis.AudioProblem):
            analysis.analyze(txt)

    def test_silence_has_no_beat(self):
        import soundfile as sf

        p = self.dir / "silence.wav"
        sf.write(str(p), [0.0] * 22050 * 8, 22050)
        with self.assertRaises(analysis.AudioProblem):
            analysis.analyze(p)

    def test_protected_file_is_refused_before_reading(self):
        p = self.dir / "Logic" / "x.wav"
        p.parent.mkdir()
        p.write_bytes(b"not audio")
        with self.assertRaises(analysis.AudioProblem) as cm:
            analysis.analyze(p)
        self.assertIn("never touch", str(cm.exception))


class Lookup(Base):
    def test_bare_names_are_found_in_search_folders_only(self):
        (self.dir / "sub").mkdir()
        self.clip("sub/kick loop.wav")
        (self.dir / "Waves").mkdir()
        self.clip("Waves/kick loop.wav")  # protected folder: never matched
        found = music_tool.find("kick loop", [self.dir])
        self.assertEqual([p.parent.name for p in found], ["sub"])
        self.assertEqual(music_tool.find("KICK LOOP.WAV", [self.dir]), found)

    def test_ambiguous_and_missing_names(self):
        for d in ("a", "b"):
            (self.dir / d).mkdir()
            self.clip(f"{d}/dup.wav")
        orig = music_tool.search_folders
        music_tool.search_folders = lambda: [self.dir]
        try:
            with self.assertRaises(analysis.AudioProblem) as cm:
                music_tool.resolve("dup.wav")
            self.assertIn("AMBIGUOUS", str(cm.exception))
            with self.assertRaises(analysis.AudioProblem):
                music_tool.resolve("nothere.wav")
        finally:
            music_tool.search_folders = orig

    def test_dropped_file(self):
        p = self.clip("dropped.wav")
        music_tool.set_drop_source(lambda: {"items": [{"kind": "file", "path": str(p)}]})
        try:
            self.assertEqual(music_tool.resolve("dropped"), p)
            self.assertEqual(music_tool.resolve("this file"), p)
        finally:
            music_tool.set_drop_source(None)
        with self.assertRaises(analysis.AudioProblem):
            music_tool.resolve("")


class Tool(Base):
    def setUp(self):
        super().setUp()
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(self.dir / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        charts.set_panel(self.panel, self.files, lambda: 4)

    def tearDown(self):
        charts.set_panel(None, None)
        super().tearDown()

    def test_card_shows_bpm_key_confidence_and_duration(self):
        p = self.clip("card.wav", 128, "E minor", 20.0, 7)
        said = music_tool.analyze_audio(str(p))
        card = self.sent[-1]
        self.assertEqual(
            (card["type"], card["title"], card["turn"]), ("panel.table", "card.wav", 4)
        )
        self.assertEqual(pn.validate(card), [])
        names = [r[0] for r in card["rows"]]
        self.assertEqual(names[:3], ["Tempo", "Key", "Duration"])
        self.assertIn("BPM", card["rows"][0][1])
        self.assertEqual(card["rows"][2][1], "0:20")
        self.assertIn("BPM", said)
        self.assertIn("side panel", said)

    def test_failures_are_spoken_not_raised(self):
        self.assertIn("couldn't find", music_tool.analyze_audio(str(self.dir / "no.wav")))
        self.assertEqual(self.sent, [])

    def test_runs_without_a_panel(self):
        charts.set_panel(None, None)
        said = music_tool.analyze_audio(str(self.clip("np.wav", 120, "D major", 15.0, 8)))
        self.assertIn("BPM", said)
        self.assertNotIn("side panel", said)

    def test_through_the_dispatcher(self):
        d = Dispatcher(dict(tools.REGISTRY), tools.SCHEMAS)
        d.begin_turn()
        out = d.dispatch(
            "analyze_audio", {"path": str(self.clip("disp.wav", 100, "A minor", 15.0, 9))}
        )
        self.assertEqual((out["agent"], out["code"]), ("audio", "completed"), out)

    def test_tool_is_offered_for_music_questions_only(self):
        names = lambda text: {s["function"]["name"] for s in tools.select_schemas(text)}  # noqa: E731
        for text in (
            "What's the BPM of kick_loop.wav?",
            "what key is pad.wav in",
            "analyze this track",
            "What tempo is my song",
        ):
            self.assertIn("analyze_audio", names(text), text)
        self.assertNotIn("analyze_audio", names("what's the weather in Springfield"))
        self.assertNotIn("analyze_audio", names("what time is it"))


if __name__ == "__main__":
    unittest.main()
