"""The failed live test of 2026-11-16, replayed: typed and spoken lines (the misheard ones through the hearing fixes), card buttons, undo, save,
open, palettes. It must end with an NES sprite made from the photo already taken, without "drop a photo", and with Julius's record able to
confirm "you already made two". Brain turns are the tool calls the routing eval checks the model makes (r231, r240, r249 to r252)."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import julius.ui_bridge.charts as charts
import julius.ui_bridge.events as ev
import julius.ui_bridge.panel as pn
import julius.ui_bridge.panel_emit as pe
import julius.ui_bridge.panelfiles as pf
from julius.art import commands, source
from julius.art import sprite_doc as sd
from julius.art import tool as art_tool
from julius.brain import session_state
from julius.brain.undo import NOTHING, UndoAgent, UndoCoordinator
from julius.runtime import heard, inputs
from tests.harness import make_session
from tests.test_sprite import synthetic

PALETTE_QUESTION = 'Hmm, I don\'t know a "Nest Brite" palette. The ones I can do are gameboy, pico8 and nes. Want one of those instead?'


class LiveTranscript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.saved = root / "Julius Saved"
        img, self.face, self.mask = synthetic(beard="full")
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG")
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(root / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/t")
        charts.set_panel(self.panel, self.files, lambda: 1)
        self.addCleanup(charts.set_panel, None, None)
        sd.STUDIO.__init__()
        session_state.reset()
        source.forget_current()
        source.forget_held()
        self.addCleanup(source.forget_held)
        self.addCleanup(source.forget_current)
        source.hold(buf.getvalue())  # the camera photo, taken once at the start
        for target, name, value in (
            (art_tool.faces, "landmarks", (self.face, self.mask)),
            (art_tool.faces, "detect", [(0.35, 0.27, 0.3, 0.52)]),
            (pe, "saved_dir", lambda *_a, **_k: self.saved),
        ):
            p = (
                mock.patch.object(target, name, side_effect=value)
                if callable(value)
                else mock.patch.object(target, name, return_value=value)
            )
            p.start()
            self.addCleanup(p.stop)
        self.last_reply = ""

    # what the runtime does with a line before the brain: the hearing fixes, then the deterministic handlers
    def hear(self, said):
        return heard.fix(said, self.last_reply, sd.STUDIO.get() is not None)

    def shortcut(self, text):
        out = (
            pe.handle(self.panel, self.files, text, str(self.saved))
            or commands.handle(text)
            or commands.palette_answer(text, self.last_reply, True)
            or commands.answer_to_open(text, self.last_reply)
        )
        if out:
            self.last_reply = out
        return out

    def test_the_whole_session(self):
        # Julius starts: the undo coordinator watches every agent from boot (a file batch from 5 October already exists)
        files_ran = []
        st = sd.STUDIO
        undo = UndoCoordinator(
            {
                "files": UndoAgent(
                    "the last file change",
                    lambda: "oct-5-batch",
                    lambda: True,
                    lambda: files_ran.append(1) or "Reversed 1 file change(s).",
                ),
                "sprite": UndoAgent(
                    "the last sprite edit",
                    lambda: (st.current, len(st.undo.get(st.current, []))),
                    lambda: bool(st.undo.get(st.current)),
                    lambda: "back" if art_tool.sprite_edit("undo") == "Undone." else None,
                ),
            }
        )
        # 13:57 typed "Make me a srpite" (a typo): a sprite from the camera photo
        self.assertEqual(self.hear("Make me a srpite"), "Make me a sprite")
        said = art_tool.image_style("dropped", style="sprite")
        self.assertIn("Sprite of camera photo", said)
        sid = sd.STUDIO.current

        # card buttons: bigger beard x2, smaller x2, wider eyes. Instant, never a turn
        before = len(self.sent)
        for op in ("bigger beard", "bigger beard", "smaller beard", "smaller beard", "wider eyes"):
            self.assertTrue(art_tool.sprite_ops(sid, [op])["ok"])
        self.assertFalse(any(m.get("type") == "transcript" for m in self.sent[before:]))

        # "Undo." went to a month-old file move; now it is the sprite, and the files are left alone
        undo.observe(7)
        self.assertEqual(undo.handle("Undo."), "Undid the last sprite edit. back")
        self.assertEqual(files_ran, [])

        # "Sunglasses in the cap." (the brain calls sprite_edit with his words)
        self.assertIn("sunglasses", art_tool.sprite_edit(self.hear("Sunglasses in the cap.")))
        self.assertEqual(
            (st.get()["params"]["glasses"], st.get()["params"]["hat"]), ("shades", "cap")
        )

        # "Save this card." then "Open my Sprite.": one recent sprite opens straight away
        self.assertIn("Saved to", self.shortcut("Save this card."))
        out = self.shortcut(self.hear("Open my Sprite."))
        self.assertIn("Opened Sprite ", out)
        self.assertNotIn("Other recent", out)

        # 16:18 a new sprite, sunglasses and a cap, saved as Sunday Roy
        art_tool.image_style("dropped", style="sprite")
        art_tool.sprite_edit("Add sunglasses and a cap.")
        self.assertIn("Saved to", self.shortcut("Save it as Sunday Roy."))

        # "Open my sprayer." / "Open my, I'm gonna spray it." / "Yes, Sprite." / "Open Sunday, Roy."
        for said in ("Open my sprayer.", "Open my, I'm gonna spray it."):
            text = self.hear(said)
            self.assertEqual(text, "Open my sprite.")
            self.assertIn("Opened Sunday Roy", self.shortcut(text))
        self.last_reply = "I'm not sure what you want me to open for the sprite. Do you mean your sprite? If so, say open my sprite."
        self.assertIn("Opened Sunday Roy", self.shortcut(self.hear("Yes, Sprite.")))
        self.assertIn("Opened Sunday Roy", self.shortcut(self.hear("Open Sunday, Roy.")))
        self.assertIsNotNone(source.current())  # opening a saved sprite never drops the photo

        # "make me a Pico 8 sprite." (the brain calls image_style with source dropped and the palette)
        self.assertEqual(self.hear("make me a Pico 8 sprite."), "make me a pico8 sprite.")
        self.assertIn(
            "pico8 palette", art_tool.image_style("dropped", style="sprite", palette="pico8")
        )

        # "Make me a Nest Brite." is "Make me an NES sprite."
        self.assertEqual(self.hear("Make me a Nest Brite."), "Make me an NES sprite.")

        # the typed run "Mess", "New", "Nes", "nes", "nes" in a few seconds: one message, not five turns
        s = make_session()
        for t in ("Mess", "New", "Nes", "nes", "nes"):
            inputs.ui_typed(s, t)
        self.assertEqual([str(x) for x in s.typed], ["Nes"])

        # after the palette question, "Miss." is NES: the NES sprite from the photo already taken, no "drop a photo"
        self.last_reply = PALETTE_QUESTION
        text = self.hear("Miss.")
        self.assertEqual(text, "NES")
        out = self.shortcut(text)
        self.assertIn("nes palette", out)
        self.assertIn("camera photo", out)
        self.assertNotRegex(out.lower(), r"drop (?:a|the) photo|which picture")
        self.assertEqual(sd.STUDIO.get()["palette"], "nes")

        # "You just made two different sprites from it.": the brain's record says so (the routing eval checks the model confirms it)
        self.assertGreaterEqual(session_state.count("sprite"), 4)
        note = session_state.reminder("")
        self.assertIn("you made 4 sprites this session", note)
        self.assertIn("the camera selfie is the current image", note)
        self.assertIn("Julius made 4 sprites this session", session_state.block(""))

    def test_a_bare_undo_with_nothing_this_session_never_touches_old_files(self):
        ran = []
        c = UndoCoordinator(
            {
                "files": UndoAgent(
                    "the last file change",
                    lambda: 1,
                    lambda: True,
                    lambda: ran.append(1) or "moved",
                )
            }
        )
        self.assertEqual(c.handle("Undo."), NOTHING)
        self.assertEqual(ran, [])


if __name__ == "__main__":
    unittest.main()
