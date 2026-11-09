import tempfile, unittest
from pathlib import Path

from julius_filename_fuzzy import closest, split
from julius_file_agent import FileAgent
from julius_file_session import FileSession

NAMES = ["budget.csv", "demo.wav", "notes.txt", "report.txt", "stats.csv"]   # what the sandbox listing showed on 2026-11-09


class Fuzzy(unittest.TestCase):
    def test_split_removes_verb_glue_plural_and_case(self):
        self.assertEqual(split("TrashReport.TXT"), ("report", "txt")); self.assertEqual(split("Reports.TXT"), ("report", "txt")); self.assertEqual(split("Notes.txt"), ("note", "txt"))
        self.assertEqual(split("DeleteTrashBudget.csv"), ("budget", "csv"))

    def test_the_real_mangled_transcripts_find_report_txt(self):
        for pattern, spoken in (("TrashReport.TXT", "Trash Report. TrashReport.TXT in the Sandbox."), ("Reports.TXT", "Trash Reports.TXT in the sandbox"), ("Reports.Trash", "Trash Reports.Trash Reports.TXT in the sandbox"),
                                ("Report.txt", "Trash Report Report.txt in Sandbox.")):
            self.assertEqual(closest(pattern, spoken, NAMES), ["report.txt"], spoken)

    def test_it_never_reaches_for_unrelated_files(self):
        self.assertEqual(closest("invoice.txt", "Trash invoice.txt in the sandbox", NAMES), []); self.assertEqual(closest("Rep.txt", "trash rep.txt", NAMES), [])

    def test_two_close_names_are_both_returned_so_he_has_to_choose(self):
        self.assertEqual(closest("TrashReport.TXT", "", ["report.txt", "reports.txt", "notes.txt"]), ["report.txt", "reports.txt"])

    def test_an_extension_that_exists_must_match(self):
        self.assertEqual(closest("report.csv", "trash report.csv", ["report.txt", "stats.csv"]), [], "csv exists here, so a .csv was meant and there is none")


class Session(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        for d in ("Documents", "Desktop", "Downloads", "dev/sandbox"): (self.root / d).mkdir(parents=True)
        for n in NAMES: (self.root / "dev/sandbox" / n).write_text("x")
        self.agent = FileAgent(self.root, self.root / "state", folders={"Documents": "write", "Desktop": "write", "Downloads": "write", "dev/sandbox": "write"}); self.agent.use_birthtime = False
        self.s = FileSession(self.agent)

    def test_each_transcript_gets_a_preview_naming_the_real_file_and_changes_nothing(self):
        for phrase in ("TrashReport. TrashReport.TXT in the Sandbox.", "TrashReport.txt in Sandbox.", "TrashReport, Report.txt in Sandbox.", "Trash Reports.TXT in the sandbox",
                       "TrashReport. TrashReport.TrashReport.TXT. Trash Reports.TXT."):
            out = self.s.handle(phrase, 1); self.assertEqual(out["code"], "preview", phrase); self.assertIn("report.txt", out["message"]); self.assertNotIn("notes.txt", out["message"])
            self.assertEqual(sorted(p.name for p in (self.root / "dev/sandbox").iterdir()), NAMES)

    def test_yes_trashes_only_that_file(self):
        self.s.handle("Trash Reports.TXT in the sandbox", 1); out = self.s.handle("yes", 2)
        self.assertEqual(out["code"], "completed"); self.assertFalse((self.root / "dev/sandbox/report.txt").exists()); self.assertTrue((self.root / "dev/sandbox/notes.txt").exists())

    def test_nothing_close_stays_no_matching_files(self):
        out = self.s.handle("Trash invoice.txt in the sandbox", 1); self.assertIn(out["code"], ("no_matches", "not_found"))


if __name__ == "__main__":
    unittest.main()
