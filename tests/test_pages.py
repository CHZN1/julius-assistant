"""Saving a page: "save this page" transcribes it word for word into a note; "rewrite it as bullet notes" makes a second, labelled note."""

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from julius.agents import pages, vision
from julius.ui_bridge import events as ev
from julius.ui_bridge import panel as pn

PAGE = '{"book": "MOBY-DICK; OR, THE WHALE", "chapter": "CHAPTER 1. Loomings.", "page": "1", "text": "# CHAPTER 1. Loomings.\\n\\nCall me Ishmael. Some years ago."}'


class Routing(unittest.TestCase):
    def test_save_this_page_is_the_camera_unless_an_image_was_dropped(self):
        r = vision.parse("Save this page")
        self.assertEqual((r.source, r.page, r.closely), ("camera", True, True))
        self.assertEqual(
            vision.parse("save this page for my research", has_dropped=True).source, "image"
        )
        self.assertEqual(
            vision.parse("transcribe this page from the camera", has_dropped=True).source, "camera"
        )
        self.assertIsNone(vision.parse("save this card"))  # cards have their own save

    def test_rewrite_needs_a_page_saved_lately(self):
        pages.LAST_PAGE.clear()
        self.assertIsNone(pages.rewrite_form("rewrite it as bullet notes"))
        pages.LAST_PAGE.update(t=time.time(), path="/x.md", title="t", text="x")
        self.addCleanup(pages.LAST_PAGE.clear)
        self.assertEqual(pages.rewrite_form("Rewrite it as bullet notes"), "bullet notes")
        self.assertEqual(pages.rewrite_form("summarize it"), "summary")
        self.assertEqual(pages.rewrite_form("rewrite that page in plain English"), "plain")
        self.assertIsNone(pages.rewrite_form("rewrite my essay about whales"))


class Saving(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {"notes_dir": self.tmp.name}
        self.addCleanup(pages.LAST_PAGE.clear)

    def save(self, raw=PAGE, panel=None, files=None):
        return pages.save_page(
            None,
            mock.Mock(return_value=(raw, 0.01)),
            b"j",
            10,
            10,
            "camera",
            "save this page",
            panel,
            files,
            3,
            self.cfg,
        )

    def test_the_note_is_word_for_word_and_never_overwritten(self):
        said = self.save()
        self.save()
        self.assertEqual(
            said,
            f"Saved page 1 of Moby-Dick; Or, The Whale, word for word: 9 words, in {Path(self.tmp.name).name}.",
        )
        notes = sorted(p.name for p in Path(self.tmp.name).iterdir())
        self.assertEqual(len(notes), 2)
        self.assertTrue(notes[1].endswith("(2).md") or notes[0].endswith("(2).md"))
        body = (Path(self.tmp.name) / notes[0]).read_text()
        self.assertIn("Call me Ishmael. Some years ago.", body)
        self.assertIn("Transcribed word for word by Julius", body)

    def test_too_little_text_is_not_saved(self):
        said = self.save('{"text": "blur"}')
        self.assertIn("couldn't read enough", said)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_the_card(self):
        hub = pn.PanelHub(ev.EventHub())
        files = mock.Mock(put_memory=mock.Mock(return_value="/p/" + "a" * 32 + ".jpg"))
        self.save(panel=hub, files=files)
        c = list(hub.cards.values())[-1]
        self.assertEqual((c["type"], c["kind"], c["words"]), ("panel.page", "verbatim", 9))
        self.assertTrue(c["saved"])
        self.assertIn("Call me Ishmael", c["text"])
        self.assertEqual(pn.validate(c), [])

    def test_rewrite_writes_a_second_note_and_leaves_the_exact_one(self):
        self.save()
        exact = Path(pages.LAST_PAGE["path"])
        before = exact.read_text()
        client = mock.Mock()
        client.messages.create.return_value = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="- Ishmael goes to sea.")],
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )
        brain = SimpleNamespace(
            config={"models": {"sonnet": "s"}}, slow_client=lambda: client, tracker=mock.Mock()
        )
        with mock.patch("julius.costs.cost_of", return_value=0.0):
            said = pages.rewrite(brain, "bullet notes", None, 4)
        self.assertIn("notes", said)
        self.assertEqual(exact.read_text(), before)
        notes = next(p for p in Path(self.tmp.name).iterdir() if "(notes)" in p.name)
        self.assertIn("Rewritten by Julius from the exact transcription", notes.read_text())
        self.assertIn("Ishmael goes to sea", notes.read_text())


if __name__ == "__main__":
    unittest.main()
