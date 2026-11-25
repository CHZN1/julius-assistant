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


class Extensions(unittest.TestCase):
    ROOTS = ("C", "C#", "Db", "D", "Eb", "E", "F", "F#", "Gb", "G", "Ab", "A", "Bb", "B")
    NEW = (
        "9",
        "11",
        "13",
        "m11",
        "m13",
        "maj13",
        "6/9",
        "maj7#11",
        "7b9",
        "7#9",
        "7#11",
        "7b13",
        "7#5",
        "7b5",
        "7alt",
    )

    def test_extended_and_altered_spelling(self):
        for sym, expect in {
            "C9": "C E G Bb D",
            "C11": "C E G Bb D F",
            "Dm11": "D F A C E G",
            "G13": "G B D F A E",
            "Cmaj13": "C E G B D A",
            "Am13": "A C E G B F#",
            "C6/9": "C E G A D",
            "G7b9": "G B D F Ab",
            "G7#9": "G B D F A#",
            "G7#11": "G B D F C#",
            "G7b13": "G B D F Eb",
            "G7alt": "G B F Ab A# Eb",
            "C7#5": "C E G# Bb",
            "C7b5": "C E Gb Bb",
            "Cmaj7#11": "C E G B F#",
            "F#7b9": "F# A# C# E G",
            "Eb7#9": "Eb G Bb Db F#",
            "Ab7b13": "Ab C Eb Gb Fb",
        }.items():
            self.assertEqual(" ".join(t.parse_chord(sym).notes), expect, sym)

    def test_every_new_type_is_spelled_by_letter_in_every_root(self):
        for root in self.ROOTS:
            letter, pc = t.parse_note(root)
            for q in self.NEW:
                c = t.build_chord(root, q)
                for note, deg, semi in zip(c.notes, c.degrees, c.offsets, strict=False):
                    self.assertEqual(note[0], t.LETTERS[(letter + deg - 1) % 7], (root, q, c.notes))
                    self.assertEqual(t.parse_note(note)[1], (pc + semi) % 12, (root, q, note))
                self.assertEqual(c.symbol, root + q)

    def test_aliases(self):
        for text, symbol in {
            "Dm7-5": "Dm7b5",
            "Dm7(b5)": "Dm7b5",
            "Cmin7b5": "Cm7b5",
            "C-7b5": "Cm7b5",
            "Cø": "Cm7b5",
            "G7alt": "G7alt",
            "Galt": "G7alt",
            "G7(b9)": "G7b9",
            "G7(#9)": "G7#9",
            "G7+5": "G7#5",
            "Caug7": "C7#5",
            "C69": "C6/9",
            "Cmin11": "Cm11",
            "Cmin13": "Cm13",
            "C-9": "Cm9",
            "CM13": "Cmaj13",
        }.items():
            self.assertEqual(t.parse_chord(text).symbol, symbol, text)
        # a hyphen between chords is a separator, but a trailing 7-5 style spelling inside a symbol stays one chord when written Dm7-5 on its own
        self.assertEqual(t.parse_chord("Dm7-5").notes, ["D", "F", "Ab", "C"])

    def test_roman_numerals_with_extensions(self):
        key = t.parse_key("C major")
        got = {
            r: t.chord_from_roman(r, key).symbol
            for r in (
                "ii9",
                "V13",
                "Imaj9",
                "V7alt",
                "V7b9",
                "ii11",
                "IVmaj13",
                "V7#9",
                "V7#11",
                "V7b13",
                "I6/9",
                "iiø",
                "vi9",
            )
        }
        self.assertEqual(
            got,
            {
                "ii9": "Dm9",
                "V13": "G13",
                "Imaj9": "Cmaj9",
                "V7alt": "G7alt",
                "V7b9": "G7b9",
                "ii11": "Dm11",
                "IVmaj13": "Fmaj13",
                "V7#9": "G7#9",
                "V7#11": "G7#11",
                "V7b13": "G7b13",
                "I6/9": "C6/9",
                "iiø": "Dm7b5",
                "vi9": "Am9",
            },
        )
        for sym in ("Dm9", "G13", "G7alt", "G7b9", "Cmaj13", "C6/9", "Dm11"):
            roman = t.roman_for(t.parse_chord(sym), key)
            self.assertEqual(t.chord_from_roman(roman, key).symbol, sym, (sym, roman))

    def test_compact_voice_leading_for_tensions(self):
        key = t.parse_key("C major")
        chords = [t.chord_from_roman(r, key) for r in ("ii9", "V13", "Imaj9")]
        for rootless in (False, True):
            voiced = t.smooth(chords, rootless_voicings=rootless)
            for c, v in zip(chords, voiced, strict=False):
                self.assertLessEqual({m % 12 for m in v.midi}, set(c.pcs))
                if rootless:
                    self.assertNotIn(c.root_pc, {m % 12 for m in v.midi})
                else:
                    self.assertEqual({m % 12 for m in v.midi}, set(c.pcs))
            for a, b in zip(voiced, voiced[1:], strict=False):
                self.assertLessEqual(sum(min(abs(n - p) for p in a.midi) for n in b.midi), 12)


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
        for sym in ("C", "Am7", "G7", "Bdim", "Dsus4"):
            c = t.parse_chord(sym)
            for v in t.voicings(c):
                got = {m % 12 for m in v.midi}
                if v.label.startswith("shell"):
                    self.assertLessEqual(got, set(c.pcs))
                else:
                    self.assertEqual(got, set(c.pcs), (sym, v.label))
                self.assertEqual(v.midi, sorted(v.midi), (sym, v.label))
            labels = [v.label for v in t.voicings(c)]
            self.assertEqual(labels[0], "root position")
            if len(c.offsets) == 4:
                self.assertIn("drop 2", labels)

    def test_big_chords_get_rootless_voicings_that_leave_out_the_root(self):
        for sym in ("Dm9", "G13", "Cmaj9", "G7alt", "Em11", "C6/9", "G7b9"):
            c = t.parse_chord(sym)
            vs = {v.label: v for v in t.voicings(c)}
            self.assertTrue(vs["root position (spread)"].midi[0] % 12 == c.root_pc, sym)
            for name in ("rootless A", "rootless B"):
                if name in vs:
                    got = {m % 12 for m in vs[name].midi}
                    self.assertNotIn(c.root_pc, got, (sym, name))
                    self.assertLessEqual(got, set(c.pcs), (sym, name))
                    self.assertTrue(
                        all(48 <= m <= 79 for m in vs[name].midi), (sym, name, vs[name].midi)
                    )
            self.assertIn("rootless A", vs, sym)
        a = t.voicings(t.parse_chord("Dm9"))
        self.assertEqual(t.names_for(a[1], a[1].midi), ["F", "A", "C", "E"])  # 3-5-7-9
        b = next(v for v in a if v.label == "rootless B")
        self.assertEqual(t.names_for(b, b.midi), ["C", "E", "F", "A"])  # 7-9-3-5


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


class Styles(unittest.TestCase):
    def test_style_lookup(self):
        from julius.music import styles as st

        for text, name in {
            "one drop reggae": "reggae (one drop)",
            "Reggae": "reggae (one drop)",
            "rocksteady": "reggae (one drop)",
            "dub bassline chords": "dub",
            "neo-soul": "neo-soul / lo-fi",
            "Neo Soul": "neo-soul / lo-fi",
            "lo-fi": "neo-soul / lo-fi",
            "Motown-style": "Motown / soul",
            "northern soul": "Motown / soul",
            "bebop": "jazz",
            "12 bar blues": "blues",
        }.items():
            self.assertEqual(st.find_style(text).name, name, text)
        self.assertIsNone(st.find_style("polka"))
        self.assertIsNone(st.find_style("the double bass"))  # 'dub' must be a whole word

    def test_every_style_progression_parses_in_every_key_and_fits_a_card(self):
        from julius.music import styles as st

        for style in st.STYLES:
            for key in ("C major", "A minor", "F# major", "Bb minor", "Eb major", "D dorian"):
                res = st.styled(style.aliases[0], key)
                card = {"type": "panel.theory", "t": 0.0, "panel_id": "p", "seq": 1, **res.card}
                self.assertEqual(pn.validate(card), [], (style.name, key))
            for lib in (style.major, style.minor):
                for _, romans in lib:
                    self.assertLessEqual(len(romans), 16)

    def test_one_drop_reggae(self):
        from julius.music import styles as st

        res = st.styled("one drop reggae")
        self.assertIn("A minor", res.facts)
        self.assertIn("no key given", res.facts)
        self.assertIn("Am (A C E) | Dm (D F A)", res.facts)
        self.assertIn("Bass notes", res.facts)
        for c in res.card["chords"]:
            self.assertTrue(
                all(58 <= m <= 79 for m in c["midi"]), c
            )  # high, clipped skank voicings
        moved = st.styled("reggae", "G major")
        self.assertIn("G (G B D) | C (C E G)", moved.facts)
        self.assertNotIn("no key given", moved.facts)

    def test_dub_has_a_bass_note_and_a_sparse_stab(self):
        from julius.music import styles as st

        res = st.styled("dub bassline chords")
        first = res.card["chords"][0]
        self.assertEqual(first["midi"][0] % 12, 9)  # A in the bass
        self.assertLessEqual(first["midi"][0], 47)
        self.assertLessEqual(len(first["midi"]), 3)
        self.assertIn("A (root), E (fifth), A (octave)", res.facts)

    def test_neo_soul_voicings_for_dm9(self):
        from julius.music import styles as st

        res = st.styled("neo-soul", chord="Dm9")
        self.assertIn("rootless A: F A C E", res.facts)
        self.assertIn("rootless B: C E F A", res.facts)
        up = st.styled("neo-soul", chord="Dm7")
        self.assertIn("Dm7 with the 9th added is Dm9", up.facts)

    def test_motown_ii_v_gets_a_real_answer_in_a_default_key(self):
        from julius.music import styles as st

        res = st.styled("Motown-style", progression="ii V")
        self.assertIn("C major", res.facts)
        self.assertIn("Dm D F A, with D doubled", res.facts)  # bass D under the triad
        self.assertEqual(res.card["chords"][0]["midi"][0] % 12, 2)
        self.assertIn("I-vi-ii-V", res.facts)
        chords = st.styled("Motown", key_text="Bb major", progression="ii V").facts
        self.assertIn("Cm", chords)

    def test_jazz_and_blues(self):
        from julius.music import styles as st

        jazz = st.styled("jazz", "Bb major").facts
        self.assertIn("F7alt", jazz)
        self.assertIn("Cm7 (C Eb G Bb) | F7 (F A C Eb) | Bbmaj7 (Bb D F A)", jazz)
        blues = st.styled("12 bar blues", "E major")
        self.assertEqual(len(blues.card["chords"]), 12)
        self.assertIn("E7 (E G# B D)", blues.facts)

    def test_unknown_style_says_what_it_knows(self):
        from julius.music import styles as st

        with self.assertRaises(t.TheoryProblem) as cm:
            st.styled("polka")
        self.assertIn("reggae", str(cm.exception))

    def test_no_artist_is_named_or_imitated(self):
        from julius.music import styles as st

        for style in st.STYLES:
            text = (style.character + " " + st.styled(style.aliases[0]).facts).lower()
            self.assertNotRegex(
                text,
                r"in the style of|sounds like|made famous|marley|motown records|berry gordy|miles davis|d'angelo",
            )


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
        d.begin_turn()
        out = d.dispatch("music_theory", {"style": "neo-soul", "chord": "Dm9"})
        self.assertEqual(out["code"], "completed", out)

    def test_style_requests_get_a_real_answer_and_a_card(self):
        for args, needle in (
            ({"style": "one drop reggae"}, "Am (A C E)"),
            ({"style": "dub"}, "A (root), E (fifth)"),
            ({"style": "neo-soul", "chord": "Dm9"}, "rootless A: F A C E"),
            ({"style": "Motown", "progression": "ii V"}, "Dm D F A, with D doubled"),
        ):
            said = music_tool.music_theory(**args)
            self.assertIn(needle, said, args)
            self.assertIn("side panel", said)
            self.assertEqual(self.sent[-1]["type"], "panel.theory")
            self.assertEqual(pn.validate(self.sent[-1]), [])
        self.assertIn("I know reggae", music_tool.music_theory(style="polka"))

    def test_tool_is_offered_for_theory_questions_only(self):
        names = lambda text: {s["function"]["name"] for s in tools.select_schemas(text)}  # noqa: E731
        for text in (
            "What chords go with A minor?",
            "Give me a chord progression in G major",
            "What's the D dorian scale?",
            "Show me voicings for Cmaj9",
            "Is Am F C G a good progression?",
            "explain the ii V I in Bb",
            "one drop reggae progression",
            "dub bassline chords",
            "neo-soul voicings for Dm9",
            "Motown-style ii-V",
            "Give me a G7alt voicing",
        ):
            self.assertIn("music_theory", names(text), text)
        for text in ("what's the weather in Springfield", "what time is it"):
            self.assertNotIn("music_theory", names(text), text)


if __name__ == "__main__":
    unittest.main()


class DoubledNotes(unittest.TestCase):
    def test_a_doubled_note_is_said_not_listed_twice(self):
        c = t.parse_chord("Dm")
        self.assertEqual(t.say_voicing(c, [50, 62, 65, 69]), "D F A, with D doubled")
        self.assertEqual(t.say_voicing(c, [62, 65, 69]), "D F A")

    def test_no_voiced_line_lists_a_note_twice(self):
        from julius.music import styles

        for text in (
            styles.styled("Motown", "C", "ii V", "").facts,
            styles.styled("neo-soul", "", "", "Dm9").facts,
            t.theory("C", "I vi ii V", "", "").facts,
        ):
            for line in text.splitlines():
                if not line.startswith("Voiced"):
                    continue
                for seg in line.split(": ", 1)[1].split(" | "):
                    notes = seg.split(", with")[0].split()[1:]  # first token is the chord symbol
                    self.assertEqual(len(notes), len(set(notes)), seg)
