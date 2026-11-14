"""The sample index only ever scans folders the user approved, never touches them, and keeps the deny-list."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from julius.music import library, tool


def tree():
    d = Path(tempfile.mkdtemp())
    mine = d / "Free CHZN1 Pack "
    mine.mkdir()
    (mine / "CHZN1_Cake_BPM160.wav").write_bytes(b"x" * 10)
    (mine / "CHZN1_Dark_F#m_BPM140.wav").write_bytes(b"x" * 10)
    (mine / "notes.txt").write_text("not audio")
    (mine / "Logic Bounces").mkdir()
    (mine / "Logic Bounces" / "secret.wav").write_bytes(b"x")
    other = d / "Purchased Kit"
    other.mkdir()
    (other / "kick.wav").write_bytes(b"x")
    outside = d / "Desktop"
    outside.mkdir()
    (outside / "loose.mp3").write_bytes(b"x")
    return d, mine, other, outside


def cfg(*folders):
    return mock.patch.object(
        library.config,
        "load_config",
        return_value={"music": {"index_allowlist": [str(f) for f in folders]}},
    )


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.d, self.mine, self.other, self.outside = tree()
        self.db = self.d / "idx.sqlite3"

    def test_nothing_is_scanned_without_an_allowlist(self):
        with cfg():
            self.assertEqual(library.allowlist(), [])
            self.assertEqual(library.scan(db=self.db)["files"], 0)
            self.assertEqual(library.search(db=self.db), [])

    def test_only_allowlisted_folders_are_indexed(self):
        with cfg(self.mine):
            r = library.scan(db=self.db)
            names = {h["name"] for h in library.search(limit=25, db=self.db)}
        self.assertEqual(r["files"], 2)
        self.assertEqual(names, {"CHZN1_Cake_BPM160", "CHZN1_Dark_F#m_BPM140"})

    def test_a_folder_outside_the_allowlist_is_refused_even_if_asked(self):
        with cfg(self.mine):
            r = library.scan([self.other], db=self.db)
            self.assertEqual(r["files"], 0)
            self.assertTrue(r["refused"])
            self.assertEqual(library.search("kick", db=self.db), [])

    def test_deny_list_still_applies_inside_an_approved_folder(self):
        with cfg(self.mine):
            library.scan(db=self.db)
            self.assertEqual(library.search("secret", db=self.db), [])

    def test_a_denied_folder_cannot_be_allowlisted(self):
        bad = self.d / "Logic"
        bad.mkdir()
        with cfg(bad):
            self.assertEqual(library.allowlist(), [])

    def test_symlinks_out_of_the_folder_are_not_followed(self):
        os.symlink(self.other / "kick.wav", self.mine / "link.wav")
        os.symlink(self.other, self.mine / "linkdir")
        with cfg(self.mine):
            library.scan(db=self.db)
            self.assertEqual(library.search("kick", db=self.db), [])
            self.assertEqual(library.search("link", db=self.db), [])

    def test_removing_a_folder_from_the_allowlist_hides_it_at_once(self):
        with cfg(self.mine):
            library.scan(db=self.db)
        with cfg(self.other):
            self.assertEqual(library.search("cake", db=self.db), [])

    def test_scanning_changes_nothing_in_the_folders(self):
        before = sorted((str(p), p.stat().st_mtime, p.stat().st_size) for p in self.d.rglob("*"))
        with cfg(self.mine):
            library.scan(db=self.db)
            library.search("cake", db=self.db)
        after = sorted((str(p), p.stat().st_mtime, p.stat().st_size) for p in self.d.rglob("*"))
        self.assertEqual(
            [b for b in before if "idx.sqlite3" not in b[0]],
            [a for a in after if "idx.sqlite3" not in a[0]],
        )

    def test_bpm_and_key_come_from_the_file_name(self):
        self.assertEqual(library.parse_name("CHZN1_Cake_BPM160"), (160, None))
        self.assertEqual(library.parse_name("Dark_F#m_BPM140"), (140, "F# minor"))
        self.assertEqual(library.parse_name("loop 90 bpm Amaj"), (90, "A major"))
        self.assertEqual(library.parse_name("kick"), (None, None))
        with cfg(self.mine):
            library.scan(db=self.db)
            self.assertEqual(
                [h["name"] for h in library.search(bpm=161, db=self.db)], ["CHZN1_Cake_BPM160"]
            )
            self.assertEqual(len(library.search(key="F# minor", db=self.db)), 1)

    def test_tool_messages(self):
        with cfg():
            self.assertIn("No sample folders or files are approved", tool.index_samples())
            self.assertIn("No sample folders or files are approved", tool.find_samples("cake"))
        with cfg(self.mine), mock.patch.object(library, "DB_PATH", self.db):
            self.assertIn("Indexed 2 audio files", tool.index_samples())
            out = tool.find_samples("", 160)
            self.assertIn("CHZN1_Cake_BPM160", out)
            self.assertNotIn("kick", out)


class FileEntryTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        pack = self.d / "Da Rocket  miniPack"
        pack.mkdir()
        self.mine = pack / "CHZN1_Chase_BPM150.mp3"
        self.mine.write_bytes(b"x" * 5)
        self.theirs = pack / "Someone Else_BPM150.mp3"
        self.theirs.write_bytes(b"x" * 5)
        self.mp3 = self.d / "CHZN1_Mercy_BPM180.mp3"
        self.wav = self.d / "CHZN1_Mercy_BPM180.wav"
        self.mp3.write_bytes(b"x")
        self.wav.write_bytes(b"xx")
        self.db = self.d / "idx.sqlite3"

    def test_a_file_entry_approves_only_that_file_not_its_neighbours(self):
        with cfg(self.mine):
            self.assertEqual(library.scan(db=self.db)["files"], 1)
            names = [h["name"] for h in library.search(limit=25, db=self.db)]
            self.assertEqual(names, ["CHZN1_Chase_BPM150"])
            self.assertEqual(library.scan([self.theirs], db=self.db)["files"], 0)

    def test_mp3_and_wav_are_one_entry_with_both_formats(self):
        with cfg(self.mp3, self.wav):
            library.scan(db=self.db)
            hits = library.search(limit=25, db=self.db)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["formats"], [".mp3", ".wav"])
        self.assertEqual(len(hits[0]["paths"]), 2)

    def test_the_tool_shows_both_formats_on_one_line(self):
        with cfg(self.mp3, self.wav), mock.patch.object(library, "DB_PATH", self.db):
            out = tool.find_samples("mercy")
        self.assertEqual(out.count("CHZN1_Mercy_BPM180"), 1)
        self.assertIn(".mp3+.wav", out)

    def test_non_audio_and_missing_file_entries_are_dropped(self):
        txt = self.d / "notes.txt"
        txt.write_text("x")
        with cfg(txt, self.d / "missing.wav"):
            self.assertEqual(library.allowlist(), [])

    def test_a_denied_file_cannot_be_allowlisted(self):
        bad = self.d / "Logic"
        bad.mkdir()
        f = bad / "bounce.wav"
        f.write_bytes(b"x")
        with cfg(f):
            self.assertEqual(library.allowlist(), [])

    def test_shipped_allowlist_has_the_approved_own_work_only(self):
        import julius.config as c

        names = (c.load_config().get("music") or {}).get("index_allowlist")
        self.assertEqual(len(names), 15)
        for n in names:
            low = n.casefold()
            self.assertTrue(
                "chzn1" in low or low.endswith("new mleodies 2"),
                n,
            )
            for banned in ("chzn drum samples", "boomin bass", "surge", "versace", "topsounds"):
                self.assertNotIn(banned, low)
