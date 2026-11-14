"""Stage 5c-music item 3: computed theory (notes, chords, Roman numerals, voicings), the panel.theory card, the tool."""

import tempfile
import unittest
from pathlib import Path

import julius.brain.tools as tools
import julius.ui_bridge.charts as charts
import julius.ui_bridge.events as ev
import julius.ui_bridge.panel as pn
import julius.ui_bridge.panelfiles as pf
from julius.brain.dispatcher import Dispatcher
from julius.music import theory as t
from julius.music import tool as music_tool


def notes(key: str) -> str:
    return " ".join(t.scale_notes(t.parse_key(key)))


class Scales(unittest.TestCase):
    def test_spelling_follows_the_key(self):
        for key, expect in {
            "A minor": "A B C D E F G",
            "Bb major": "Bb C D Eb F G A",
            "F# major": "F# G# A# B C# D# E#",
            "Gb major": "Gb Ab Bb Cb Db Eb F",
            "C# minor": "C# D# E F# G# A B",
            "D dorian": "D E F G A B C",
            "E phrygian": "E F G A B C D",
            "F lydian": "F G A B C D E",
            "G mixolydian": "G A B C D E F",
            "B locrian": "B C D E F G A",
            "D harmonic minor": "D E F G A Bb C#",
            "A melodic minor": "A B C D E F# G#",
            "C pentatonic": "C D E G A",
            "A minor pentatonic": "A C D E G",
            "A blues": "A C D Eb E G",
        }.items():
            self.assertEqual(notes(key), expect, key)

    def test_key_names_are_forgiving(self):
        for text, mode in (
            ("Am", "minor"),
            ("a minor", "minor"),
            ("A", "major"),
            ("C major", "major"),
            ("f# MIN", "minor"),
            ("Bb", "major"),
            ("bb minor", "minor"),
        ):
            self.assertEqual(t.parse_key(text).mode, mode, text)
        self.assertEqual(t.parse_key("bb minor").tonic, "Bb")

    def test_every_key_has_one_letter_per_degree(self):
        for tonic in ("C", "C#", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B"):
            for mode in ("major", "minor", "dorian", "mixolydian", "lydian", "phrygian"):
                names = t.scale_notes(t.parse_key(f"{tonic} {mode}"))
                self.assertEqual(len({n[0] for n in names}), 7, (tonic, mode, names))
                self.assertNotIn("###", "".join(names))

    def test_bad_keys_are_speakable(self):
        for bad in ("", "H major", "A wobbly"):
            with self.assertRaises(t.TheoryProblem):
                t.parse_key(bad)


class Chords(unittest.TestCase):
    def test_diatonic_chords(self):
        got = [(c.roman, c.symbol) for c in t.diatonic(t.parse_key("A minor"))]
        self.assertEqual(
            got,
            [
                ("i", "Am"),
                ("ii°", "Bdim"),
                ("III", "C"),
                ("iv", "Dm"),
                ("v", "Em"),
                ("VI", "F"),
                ("VII", "G"),
            ],
        )
        got = [c.symbol for c in t.diatonic(t.parse_key("Ab major"), True)]
        self.assertEqual(got, ["Abmaj7", "Bbm7", "Cm7", "Dbmaj7", "Eb7", "Fm7", "Gm7b5"])
        self.assertEqual(
            [c.roman for c in t.diatonic(t.parse_key("C major"))],
            ["I", "ii", "iii", "IV", "V", "vi", "vii°"],
        )

    def test_chord_spelling(self):
        for sym, expect in {
            "Cmaj9": "C E G B D",
            "F#m7b5": "F# A C E",
            "Bb7": "Bb D F Ab",
            "Cdim7": "C Eb Gb Bbb",
            "Esus4": "E A B",
            "G+": "G B D#",
            "D6": "D F# A B",
            "Abm": "Ab Cb Eb",
            "Dm9": "D F A C E",
            "C5": "C G",
        }.items():
            self.assertEqual(" ".join(t.parse_chord(sym).notes), expect, sym)

    def test_every_chord_type_has_the_right_pitch_classes_in_every_key(self):
        for root in ("C", "C#", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B"):
            base = t.parse_note(root)[1]
            for q, tones in t.CHORDS.items():
                c = t.build_chord(root, q)
                self.assertEqual(
                    [t.parse_note(n)[1] for n in c.notes],
                    [(base + s) % 12 for _, s in tones],
                    (root, q),
                )

    def test_aliases_and_slash_chords(self):
        self.assertEqual(t.parse_chord("Cmin7").symbol, "Cm7")
        self.assertEqual(t.parse_chord("Dø").symbol, "Dm7b5")
        c = t.parse_chord("Am/G")
        self.assertEqual((c.bass, c.symbol), ("G", "Am/G"))
        self.assertEqual(t.voicings(c)[0].midi[0] % 12, 7)

    def test_unknown_chords_are_refused(self):
        for bad in ("Xm7", "Cwobble", "H"):
            with self.assertRaises(t.TheoryProblem):
                t.parse_chord(bad)

    def test_roman_numerals_both_ways(self):
        key = t.parse_key("Bb major")
        self.assertEqual(
            [c.symbol for c in (t.chord_from_roman(r, key) for r in ("ii7", "V7", "Imaj7"))],
            ["Cm7", "F7", "Bbmaj7"],
        )
        a = t.parse_key("A minor")
        self.assertEqual(
            [
                t.chord_from_roman(r, a).symbol
                for r in ("i", "VI", "III", "VII", "iv", "V", "iiø", "bII")
            ],
            ["Am", "F", "C", "G", "Dm", "E", "Bm7b5", "Bb"],
        )
        c = t.parse_key("C major")
        self.assertEqual(t.roman_for(t.parse_chord("Bb"), c), "bVII")
        self.assertEqual(t.roman_for(t.parse_chord("F#dim"), c), "#iv°")
        self.assertEqual(t.roman_for(t.parse_chord("G7"), c), "V7")


class Progressions(unittest.TestCase):
    def test_roman_input_needs_a_key(self):
        with self.assertRaises(t.TheoryProblem) as cm:
            t.analyse("ii V I")
        self.assertIn("Which key", str(cm.exception))

    def test_chord_input_infers_a_key_and_lists_alternatives(self):
        key, chords, others = t.analyse("Am F C G")
        self.assertEqual(key.short, "A minor")
        self.assertEqual([c.roman for c in chords], ["i", "VI", "III", "VII"])
        self.assertIn("C major", [k.short for k in others])
        key, chords, _ = t.analyse("C G Am F")
        self.assertEqual(
            (key.short, [c.roman for c in chords]), ("C major", ["I", "V", "vi", "IV"])
        )

    def test_separators(self):
        for text in (
            "I V vi IV",
            "I-V-vi-IV",
            "I – V – vi – IV",
            "I, V, vi, IV",
            "I → V → vi → IV",
        ):
            self.assertEqual(len(t.analyse(text, "G major")[1]), 4, text)

    def test_too_long_or_empty(self):
        for text in ("", " ", " ".join(["C"] * 17)):
            with self.assertRaises(t.TheoryProblem):
                t.analyse(text, "C major")

    def test_voice_leading_is_smooth_and_in_range(self):
        _, chords, _ = t.analyse("C Am F G", "C major")
        voiced = t.smooth(chords)
        self.assertEqual(sorted(voiced[0].midi)[0] % 12, 0)  # first chord in root position
        for a, b in zip(voiced, voiced[1:], strict=False):
            self.assertLessEqual(
                sum(min(abs(n - p) for p in a.midi) for n in b.midi), 6, (a.symbol, b.symbol)
            )
        for v in voiced:
            self.assertTrue(all(45 <= m <= 76 for m in v.midi), v.midi)
        # the sounding pitch classes are exactly the chord's
        for c, v in zip(chords, voiced, strict=False):
            self.assertEqual({m % 12 for m in v.midi}, set(c.pcs))

    def test_voicings_contain_exactly_the_chord_tones(self):
        for sym in ("C", "Am7", "G7", "Cmaj9", "Bdim", "Dsus4"):
            c = t.parse_chord(sym)
            for v in t.voicings(c):
                got = {m % 12 for m in v.midi}
                if v.label == "shell":
                    self.assertLessEqual(got, set(c.pcs))
                else:
                    self.assertEqual(got, set(c.pcs), (sym, v.label))
                self.assertEqual(v.midi, sorted(v.midi), (sym, v.label))
            labels = [v.label for v in t.voicings(c)]
            self.assertEqual(labels[0], "root position")
            if len(c.offsets) >= 4:
                self.assertIn("drop 2", labels)


class Results(unittest.TestCase):
    def test_every_entry_point_builds_a_valid_card(self):
        for args in (
            {"key": "A minor"},
            {"key": "G major"},
            {"scale": "D dorian"},
            {"scale": "A blues"},
            {"chord": "Cmaj7"},
            {"chord": "F#m7b5", "key": "G major"},
            {"progression": "ii7 V7 Imaj7", "key": "Bb major"},
            {"progression": "Am F C G"},
        ):
            res = t.theory(**args)
            card = {
                "type": "panel.theory",
                "t": 0.0,
                "panel_id": "p1",
                "seq": 1,
                "turn": 1,
                **res.card,
            }
            self.assertEqual(pn.validate(card), [], args)
            self.assertTrue(res.facts)

    def test_key_overview_names_functions_and_progressions(self):
        facts = t.theory(key="A minor").facts
        for needle in (
            "Am",
            "Bdim",
            "Relative major: C major",
            "natural-minor four",
            "Borrowed from harmonic minor",
            "E G# B",
        ):
            self.assertIn(needle, facts)

    def test_modes_report_their_parent(self):
        self.assertIn("mode 2 of C major", t.theory(scale="D dorian").facts)
        self.assertIn("F#->F", t.theory(scale="D dorian").facts)

    def test_nothing_in_the_facts_imitates_anyone(self):
        for args in ({"key": "C major"}, {"progression": "I V vi IV", "key": "C major"}):
            self.assertNotRegex(
                t.theory(**args).facts.lower(), r"in the style of|sounds like|made famous"
            )

    def test_nothing_asked(self):
        with self.assertRaises(t.TheoryProblem):
            t.theory()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        hub = ev.EventHub()
        self.sent = []
        hub._send = lambda m: self.sent.append(m)
        self.files = pf.PanelFiles(Path(self.tmp.name) / "p")
        self.panel = pn.PanelHub(hub, base="http://127.0.0.1:1/tok")
        charts.set_panel(self.panel, self.files, lambda: 6)

    def tearDown(self):
        charts.set_panel(None, None)
        self.tmp.cleanup()


class Card(Base):
    def test_hub_cleans_and_bounds(self):
        pid = self.panel.theory(
            {
                "title": "x" * 500,
                "kind": "nonsense",
                "key": "A minor",
                "scale_notes": ["A"] * 30,
                "chords": [
                    {
                        "symbol": "Am",
                        "roman": "i",
                        "label": "",
                        "notes": ["A", "C", "E"],
                        "midi": [57, 60, 64, 5, 999],
                        "root": 21,
                    }
                ]
                * 40,
            },
            turn=3,
        )
        self.assertIsNotNone(pid)
        card = self.sent[-1]
        self.assertEqual((card["type"], card["kind"], card["turn"]), ("panel.theory", "key", 3))
        self.assertEqual(
            (len(card["chords"]), len(card["scale_notes"]), len(card["title"]) <= 200),
            (16, 12, True),
        )
        self.assertEqual(card["chords"][0]["midi"], [57, 60, 64])
        self.assertEqual(card["chords"][0]["root"], 9)
        self.assertEqual(pn.validate(card), [])

    def test_validator_rejects_bad_cards(self):
        good = t.theory(chord="C").card
        msg = {"type": "panel.theory", "t": 0.0, "panel_id": "p", "seq": 1, **good}
        self.assertEqual(pn.validate(msg), [])
        self.assertTrue(pn.validate({**msg, "kind": "x"}))
        self.assertTrue(pn.validate({**msg, "chords": []}))
        bad = {**good, "chords": [{**good["chords"][0], "midi": [1, 2]}]}
        self.assertTrue(pn.validate({**msg, **bad}))


class Tool(Base):
    def test_tool_publishes_a_card_and_returns_exact_facts(self):
        said = music_tool.music_theory(progression="ii7 V7 Imaj7", key="Bb major")
        self.assertIn("Cm7 = C Eb G Bb", said)
        self.assertIn("never add or change one from memory", said)
        self.assertIn("side panel", said)
        card = self.sent[-1]
        self.assertEqual(
            (card["type"], card["kind"], card["turn"]), ("panel.theory", "progression", 6)
        )
        self.assertEqual(pn.validate(card), [])

    def test_problems_are_spoken(self):
        self.assertIn("Which key", music_tool.music_theory(progression="ii V I"))
        self.assertIn("couldn't read the chord", music_tool.music_theory(chord="Qzz"))
        self.assertEqual(self.sent, [])

    def test_works_without_a_panel(self):
        charts.set_panel(None, None)
        said = music_tool.music_theory(scale="E phrygian")
        self.assertIn("E F G A B C D", said)
        self.assertNotIn("side panel", said)

    def test_through_the_dispatcher(self):
        d = Dispatcher(dict(tools.REGISTRY), tools.SCHEMAS)
        d.begin_turn()
        out = d.dispatch("music_theory", {"key": "A minor"})
        self.assertEqual((out["agent"], out["code"]), ("theory", "completed"), out)

    def test_tool_is_offered_for_theory_questions_only(self):
        names = lambda text: {s["function"]["name"] for s in tools.select_schemas(text)}  # noqa: E731
        for text in (
            "What chords go with A minor?",
            "Give me a chord progression in G major",
            "What's the D dorian scale?",
            "Show me voicings for Cmaj9",
            "Is Am F C G a good progression?",
            "explain the ii V I in Bb",
        ):
            self.assertIn("music_theory", names(text), text)
        for text in ("what's the weather in Fresno", "what time is it"):
            self.assertNotIn("music_theory", names(text), text)


if __name__ == "__main__":
    unittest.main()
