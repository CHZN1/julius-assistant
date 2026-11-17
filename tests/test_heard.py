"""Hearing fixes for art words, the one-word palette answer, the open-after-question rule, and Julius's session record."""

import time
import unittest

from julius.art import commands, source
from julius.art import sprite_doc as sd
from julius.brain import session_state
from julius.runtime import heard

Q = "The ones I can do are gameboy, pico8 and nes. Want one of those instead?"


class Heard(unittest.TestCase):
    def test_the_live_mishearings(self):
        for said, last, active, meant in (
            ("Open my sprayer.", "", True, "Open my sprite."),
            ("Open my, I'm gonna spray it.", "", True, "Open my sprite."),
            ("Make me a Nest Brite.", "", True, "Make me an NES sprite."),
            ("Miss.", Q, True, "NES"),
            ("Mess.", Q, True, "NES"),
            ("Nes", Q, False, "NES"),
            ("Make me a srpite", "", False, "Make me a sprite"),
            ("make me a Pico 8 sprite.", "", True, "make me a pico8 sprite."),
            ("make me a ness sprite", "", True, "make me an NES sprite"),
        ):
            self.assertEqual(heard.fix(said, last, active), meant, said)

    def test_ordinary_words_are_left_alone(self):
        for said in (
            "I miss you.",
            "That was out of spite.",
            "Open my email.",
            "Make me a sandwich.",
            "What a mess.",
            "Mess.",
        ):
            self.assertEqual(heard.fix(said, "", False), said, said)
        self.assertEqual(heard.fix("Miss.", "", False), "Miss.")  # only after a palette question

    def test_the_whisper_hint_has_the_art_words(self):
        for w in (
            "sprite",
            "NES",
            "Pico 8",
            "pico8",
            "Game Boy",
            "chibi",
            "palette",
            "pixel art",
            "pixelate",
            "dither",
            "outline",
            "goatee",
            "afro",
        ):
            self.assertIn(w, heard.ART_VOCABULARY)


class Rules(unittest.TestCase):
    def test_palette_answers_need_a_context(self):
        self.assertIsNone(commands.palette_answer("NES", "", False))
        self.assertIsNone(commands.palette_answer("NES is a console from 1985", Q, True))

    def test_open_answers_need_the_question(self):
        self.assertIsNone(commands.answer_to_open("Yes, sprite.", "Sure, what do you want?"))
        self.assertIsNone(
            commands.answer_to_open("delete everything", "Which sprite should I open?")
        )


class Record(unittest.TestCase):
    def setUp(self):
        session_state.reset()
        sd.STUDIO.__init__()
        source.forget_current()
        self.addCleanup(source.forget_current)

    def test_nothing_done_means_no_record(self):
        self.assertEqual(session_state.block(""), "")
        self.assertEqual(session_state.reminder(""), "")

    def test_the_record_lists_what_was_made_and_the_current_image(self):
        source._current = {"kind": "camera", "label": "camera photo", "at": time.time()}
        session_state.record("sprite", "made a sprite (gameboy, 24 px) from camera photo")
        session_state.record("sprite", "made a sprite (pico8, 16 px) from camera photo")
        block = session_state.block("")
        self.assertIn("Julius made 2 sprites this session", block)
        self.assertIn("never deny", block)
        note = session_state.reminder("")
        self.assertIn("you made 2 sprites this session", note)
        self.assertIn("never ask for a photo", note)

    def test_after_a_question_the_next_turn_acts(self):
        source._current = {"kind": "camera", "label": "camera photo", "at": time.time()}
        self.assertIn("do not ask another", session_state.block("Which one do you mean?"))
        self.assertIn(
            "act on the most likely reading", session_state.reminder("Which one do you mean?")
        )


if __name__ == "__main__":
    unittest.main()
