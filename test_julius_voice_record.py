import json, re, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "evals"))
import voice_record as vr


class ScenarioFileTests(unittest.TestCase):
    def setUp(self): self.sc = vr.load_scenarios()

    def test_ten_conversations_with_unique_ids_and_turns(self):
        self.assertEqual(len(self.sc), 10); ids = [s["id"] for s in self.sc]; self.assertEqual(len(set(ids)), 10)
        for s in self.sc:
            self.assertTrue(s["turns"] and s["title"], s["id"])
            for t in s["turns"]: self.assertTrue(t["say"].strip() and t["expect"]["kind"], (s["id"], t))

    def test_every_expectation_regex_compiles(self):
        for s in self.sc:
            for t in s["turns"]:
                for rx in t["expect"].get("must", []) + t["expect"].get("must_not", []): re.compile(rx, re.I)

    def test_reference_text_drops_stage_directions(self):
        self.assertEqual(vr.reference_text({"say": "Julius, what time is it in [pause] London?"}), "Julius, what time is it in London?")
        self.assertEqual(vr.reference_text({"say": "x [pause] y", "ref": "given"}), "given")

    def test_the_planned_coverage_is_there(self):
        kinds = {t["expect"]["kind"] for s in self.sc for t in s["turns"]}
        for needed in ("followup", "correction", "confirm", "cancel", "undo", "no_fabrication", "interrupt", "ignored"): self.assertIn(needed, kinds)
        self.assertTrue(any(t.get("when") == "pause" for s in self.sc for t in s["turns"])); self.assertTrue(any(t.get("when") == "during_reply" for s in self.sc for t in s["turns"]))

    def test_nothing_in_the_scenarios_touches_real_files_beyond_the_sandbox_copy_and_discard(self):
        text = json.dumps(self.sc).lower()
        for word in ("music", "logic", "garageband", "btc", "crypto", "documents", "merge"): self.assertNotIn(word, text)


class HelperTests(unittest.TestCase):
    def test_preflight_names_missing_files(self):
        d = Path(tempfile.mkdtemp()); self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        sc = {"pre": {"sandbox_files": ["report.txt"], "projects": ["demo_calc"]}}
        self.assertEqual(len(vr.preflight(sc, d / "sb", d / "dev")), 2)
        (d / "sb").mkdir(); (d / "sb" / "report.txt").write_text("x"); (d / "dev" / "demo_calc").mkdir(parents=True); self.assertEqual(vr.preflight(sc, d / "sb", d / "dev"), [])

    def test_take_folders_increment(self):
        d = Path(tempfile.mkdtemp()); self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        (d / "s01__take1").mkdir(); self.assertEqual(vr.next_take(d, "s01").name, "s01__take2")

    def test_select_by_prefix(self):
        self.assertEqual([s["id"][:3] for s in vr.select(vr.load_scenarios(), ["s03", "s07"])], ["s03", "s07"])

    def test_delete_all_needs_the_exact_phrase_and_only_touches_its_folder(self):
        d = Path(tempfile.mkdtemp()); self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        root = d / "voice_data"; (root / "2026-11-06" / "s01__take1").mkdir(parents=True); (root / "2026-11-06" / "s01__take1" / "scenario.json").write_text("{}"); (d / "keep.txt").write_text("keep")
        self.assertEqual(vr.disk_usage(root)["takes"], 1)
        self.assertFalse(vr.delete_all(root, "yes")[0]); self.assertFalse(vr.delete_all(root, None)[0]); self.assertTrue(root.exists())
        ok, msg = vr.delete_all(root, "delete all recordings"); self.assertTrue(ok); self.assertIn("1 takes", msg); self.assertFalse(root.exists()); self.assertTrue((d / "keep.txt").exists())


if __name__ == "__main__":
    unittest.main()
