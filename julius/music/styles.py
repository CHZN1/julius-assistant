"""Genre and era templates for theory help: the progressions, voicing habits and character a style is known for, realised in any key with the same computed notes.

These are general harmonic conventions of a genre, never a named artist's style: a request to imitate a named living artist is declined elsewhere, and no song or artist is quoted here.
Voicing styles: skank (short high upstroke triads), dub (bass plus a sparse shell), motown (triads over a moving bass), rootless (3-5-7-9 for the piano)."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from julius.music import theory as t


@dataclass(frozen=True)
class Style:
    name: str
    aliases: tuple[str, ...]
    default_key: str
    voicing: str  # skank | dub | motown | rootless | smooth
    character: str
    major: tuple[tuple[str, tuple[str, ...]], ...] = ()
    minor: tuple[tuple[str, tuple[str, ...]], ...] = ()
    bass_notes: bool = False
    enrich: bool = False  # add the 9th to plain 7th chords when voicing a single chord


STYLES: tuple[Style, ...] = (
    Style(
        "reggae (one drop)",
        ("one drop", "one-drop", "roots reggae", "reggae", "rocksteady", "ska"),
        "A minor",
        "skank",
        "One-drop reggae keeps the harmony simple (two to four chords) and the feel offbeat: guitar or keys chop short chords on the offbeats (the skank), the drums leave beat one empty and land on beat three, and the bass plays rounded, melodic lines that carry the harmony. Keep chord voicings high, small and clipped.",
        major=(
            ("two-chord vamp", ("I", "IV")),
            ("I-IV-I-V cycle", ("I", "IV", "I", "V")),
            ("I-vi-IV-V turnaround", ("I", "vi", "IV", "V")),
        ),
        minor=(
            ("minor vamp", ("i", "iv")),
            ("descending minor", ("i", "VII", "VI", "VII")),
            ("i-iv-i-v", ("i", "iv", "i", "v")),
        ),
        bass_notes=True,
    ),
    Style(
        "dub",
        ("dub",),
        "A minor",
        "dub",
        "Dub strips reggae down: one or two chords, heavy melodic bass built on the root and fifth (with the 3rd or flat 7th passing through), sparse short chord stabs, often minor sevenths, that drop in and out, with space and echo doing the arranging.",
        major=(("two-chord vamp", ("I", "IV")), ("I to bVII", ("I", "bVII"))),
        minor=(
            ("sparse minor vamp", ("i7", "iv7")),
            ("i to VII", ("i7", "VII")),
            ("dub descent", ("i7", "VII", "VI", "VII")),
        ),
        bass_notes=True,
    ),
    Style(
        "neo-soul / lo-fi",
        ("neo soul", "neo-soul", "neosoul", "lo-fi", "lofi", "lo fi"),
        "C major",
        "rootless",
        "Neo-soul and lo-fi lean on extended chords (9ths, 11ths, 13ths) in rootless voicings, so the bass owns the root, with smooth stepwise voice leading and ii-V motion that avoids the obvious resolution.",
        major=(
            ("ii-V-I with extensions", ("ii9", "V13", "Imaj9")),
            ("lush turnaround", ("Imaj9", "vi9", "ii9", "V13")),
            ("stepwise descent", ("IVmaj9", "iii7", "ii9", "Imaj9")),
        ),
        minor=(
            ("minor ii-V-i", ("iiø", "V7b9", "i9")),
            ("minor vamp", ("i9", "iv9")),
            ("rising colour", ("i9", "iv9", "VII13", "IIImaj9")),
        ),
        enrich=True,
    ),
    Style(
        "Motown / soul",
        ("motown", "60s soul", "northern soul", "soul"),
        "C major",
        "motown",
        "Motown-era soul uses mostly triads and plain sevenths over a bass that walks or climbs stepwise (often under inverted chords, such as I over its 3rd), strong I-vi-ii-V and ii-V-I turnarounds, and close voicings so the bass line does the moving.",
        major=(
            ("I-vi-ii-V", ("I", "vi", "ii", "V")),
            ("climbing bass I-iii-IV-V", ("I", "iii", "IV", "V")),
            ("ii-V-I", ("ii7", "V7", "I")),
        ),
        minor=(("minor turnaround", ("i", "VI", "iv", "V")),),
    ),
    Style(
        "jazz",
        ("jazz", "bebop", "swing"),
        "C major",
        "rootless",
        "Jazz harmony is built on ii-V-I with seventh chords, altered dominants (7b9, 7alt) leading into minor chords, and rootless piano voicings (A and B) alternated so the top line moves smoothly.",
        major=(
            ("ii-V-I", ("ii7", "V7", "Imaj7")),
            ("ii-V-I with an altered dominant", ("ii7", "V7alt", "Imaj7")),
            ("turnaround", ("Imaj7", "vi7", "ii7", "V7")),
        ),
        minor=(
            ("minor ii-V-i", ("iiø", "V7b9", "i6")),
            ("minor ii-V-i, altered", ("iiø", "V7alt", "i6")),
        ),
        enrich=True,
    ),
    Style(
        "blues",
        ("blues", "12 bar", "12-bar", "twelve bar"),
        "A major",
        "smooth",
        "The 12-bar blues puts dominant sevenths on I, IV and V in a fixed 12-bar cycle; the quick-change version goes to the IV chord in bar 2.",
        major=(
            (
                "12-bar blues",
                ("I7", "I7", "I7", "I7", "IV7", "IV7", "I7", "I7", "V7", "IV7", "I7", "V7"),
            ),
            (
                "quick-change 12-bar",
                ("I7", "IV7", "I7", "I7", "IV7", "IV7", "I7", "I7", "V7", "IV7", "I7", "V7"),
            ),
        ),
        minor=(
            (
                "minor 12-bar blues",
                ("i7", "i7", "i7", "i7", "iv7", "iv7", "i7", "i7", "VI7", "V7", "i7", "V7"),
            ),
        ),
    ),
)


def find_style(text: str) -> Style | None:
    low = " " + re.sub(r"[^a-z0-9#\- ]+", " ", (text or "").lower()) + " "
    best: tuple[int, Style] | None = None
    for st in STYLES:
        for alias in st.aliases:
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", low) and (
                best is None or len(alias) > best[0]
            ):
                best = (len(alias), st)
    return best[1] if best else None


def style_names() -> str:
    return ", ".join(s.name for s in STYLES)


# ---- voicings ------------------------------------------------------------------------------------------------------------------------------------------------
def _triad_tones(c: t.Chord) -> list[tuple[int, int]]:
    tones = [(d, o) for d, o in zip(c.degrees, c.offsets, strict=False) if d in (1, 2, 3, 4, 5)]
    return (
        sorted(tones, key=lambda x: x[0])[:3] or list(zip(c.degrees, c.offsets, strict=False))[:3]
    )


def _rotations(midi: list[int]) -> list[list[int]]:
    out, cur = [midi], midi
    for _ in range(len(midi) - 1):
        cur = sorted(cur[1:] + [cur[0] + 12])
        out.append(cur)
    return out


def skank(chords: list[t.Chord]) -> list[t.Chord]:
    """Short upstroke triads high on the keyboard, each in the inversion nearest the last."""
    prev, out = None, []
    for c in chords:
        base = t._upward(60, _triad_tones(c), c.root_pc)
        cands = [[x + s for x in r] for r in _rotations(base) for s in (-12, 0)]
        cands = [v for v in cands if min(v) >= 58 and max(v) <= 79]
        pick = min(
            cands,
            key=lambda v: abs(sum(v) / len(v) - 68) if prev is None else (t._cost(prev, v), v[0]),
        )
        prev = pick
        out.append(replace(c, midi=pick, label="skank: short, high, on the offbeats"))
    return out


def _bass_root(c: t.Chord) -> int:
    return 36 + (c.root_pc - 36) % 12


def dub(chords: list[t.Chord]) -> list[t.Chord]:
    """The bass note low, plus a sparse stab of the 3rd and 7th (or the 3rd and 5th) above middle C."""
    out = []
    for c in chords:
        pairs = list(zip(c.degrees, c.offsets, strict=False))
        pick = [p for p in pairs if p[0] in (2, 3, 4)][:1] + (
            [p for p in pairs if p[0] == 7][:1] or [p for p in pairs if p[0] == 5][:1]
        )
        upper = t._upward(60, pick, c.root_pc) if pick else []
        out.append(
            replace(c, midi=[_bass_root(c)] + upper, label="dub: bass note plus a sparse stab")
        )
    return out


def motown(chords: list[t.Chord]) -> list[t.Chord]:
    """A bass note under smoothly voiced close triads (sevenths kept when the chord has one)."""
    upper = t.smooth(
        [
            replace(
                c,
                offsets=[o for d, o in zip(c.degrees, c.offsets, strict=False) if d <= 7],
                degrees=[d for d in c.degrees if d <= 7],
            )
            for c in chords
        ],
        low=55,
        high=76,
    )
    return [
        replace(c, midi=[_bass_root(c)] + u.midi, label="Motown: triads over a moving bass")
        for c, u in zip(chords, upper, strict=False)
    ]


def apply_voicing(style: Style, chords: list[t.Chord]) -> list[t.Chord]:
    if style.voicing == "skank":
        return skank(chords)
    if style.voicing == "dub":
        return dub(chords)
    if style.voicing == "motown":
        return motown(chords)
    if style.voicing == "rootless":
        return t.smooth(chords, rootless_voicings=True)
    return t.smooth(chords)


_UPGRADE = {"m7": "m9", "maj7": "maj9", "7": "9", "m": "m7", "": "maj7"}


def enriched(c: t.Chord) -> tuple[t.Chord, str]:
    """A neo-soul or jazz player adds a colour tone: m7 -> m9, maj7 -> maj9, 7 -> 9. Returns the new chord and a note about the change."""
    q = _UPGRADE.get(c.quality)
    if q is None or c.quality in ("m", ""):
        return c, ""
    new = t.build_chord(c.root, q, c.bass)
    return replace(new, roman=c.roman), f"{c.symbol} with the 9th added is {new.symbol}"


# ---- the answer ----------------------------------------------------------------------------------------------------------------------------------------------
def _set(style: Style, key: t.Key) -> tuple[tuple[str, tuple[str, ...]], ...]:
    minorish = key.mode in (
        "minor",
        "dorian",
        "phrygian",
        "locrian",
        "harmonic minor",
        "melodic minor",
        "minor pentatonic",
        "blues",
    )
    lib = style.minor if minorish else style.major
    return lib or (style.major if minorish else style.minor)


def _line(label: str, chords: list[t.Chord]) -> str:
    prog = " - ".join(c.roman for c in chords)
    spelled = " | ".join(c.symbol + " (" + " ".join(c.notes) + ")" for c in chords)
    return f"  {label}: {prog} = {spelled}"


def _bass(chords: list[t.Chord]) -> str:
    parts = []
    for c in chords:
        fifth = next((n for d, n in zip(c.degrees, c.notes, strict=False) if d == 5), None)
        parts.append(
            f"{c.symbol}: {c.notes[0]} (root)"
            + (f", {fifth} (fifth)" if fifth else "")
            + f", {c.notes[0]} (octave)"
        )
    return "Bass notes to build the line on: " + "; ".join(parts) + "."


def styled(style_text: str, key_text: str = "", progression: str = "", chord: str = "") -> t.Result:
    st = find_style(style_text)
    if st is None:
        raise t.TheoryProblem(
            f"I don't have a template for {style_text.strip()!r} yet. I know {style_names()}. I can still do a key, scale, chord or progression in any style of your choosing."
        )
    note = ""
    if chord.strip():
        c = t.parse_chord(chord)
        key = t.parse_key(key_text) if key_text.strip() else None
        if key:
            c.roman = t.roman_for(c, key)
        chords = [c]
        if st.enrich:
            c2, why = enriched(c)
            if why:
                chords, note = [c2], why + " (the colour tone this style adds)."
        c = chords[0]
        if st.voicing == "rootless":
            vs = [v for v in t.voicings(c) if v.label.startswith(("rootless", "shell"))]
            if not any(v.label.startswith("rootless") for v in vs):
                vs = t.voicings(c)
        elif st.voicing in ("skank", "dub", "motown"):
            vs = apply_voicing(st, [c])
        else:
            vs = t.voicings(c)
        lines = [
            f"{st.name} voicings for {c.symbol}: {' '.join(c.notes)}."
            + (f" {note}" if note else ""),
            "Voicings (lowest to highest):",
        ]
        lines += [f"  {v.label}: {' '.join(t.names_for(v, v.midi))}" for v in vs]
        lines.append(f"Style notes: {st.character}")
        if st.bass_notes:
            lines.append(_bass([c]))
        return t.Result(
            "\n".join(lines),
            t.piano_card(
                f"{c.symbol}: {st.name} voicings", "chord", key.short if key else "", [], vs
            ),
        )
    key_given = bool(key_text.strip())
    key = t.parse_key(key_text) if key_given else t.parse_key(st.default_key)
    default_note = (
        ""
        if key_given
        else f" (no key given, so I used {key.short}; say another key and I'll move it)"
    )
    if progression.strip():
        tokens = t.split_progression(progression)
        if all(t.is_roman(x) for x in tokens):
            chords = [t.chord_from_roman(x, key) for x in tokens]
        else:
            key_for, chords, _ = t.analyse(progression, key_text)
            key, default_note = key_for, ""
        voiced = apply_voicing(st, chords)
        lines = [f"{st.name} treatment of {' '.join(tokens)} in {key.short}{default_note}:"]
        lines += [f"  {t.chord_line(c)}: {t.function_of(c.roman)}" for c in chords]
        lines.append(
            "Voiced for the style (lowest to highest): "
            + " | ".join(
                f"{c.symbol} {' '.join(t.names_for(c, v.midi))}"
                for c, v in zip(chords, voiced, strict=False)
            )
        )
        lines.append(f"Style notes: {st.character}")
        if st.bass_notes:
            lines.append(_bass(chords))
        ideas = _set(st, key)
        if ideas:
            lines.append("Other progressions in this style, in the same key:")
            lines += [_line(n, [t.chord_from_roman(r, key) for r in rs]) for n, rs in ideas]
        title = f"{' - '.join(c.roman for c in chords)}: {st.name}"
        return t.Result(
            "\n".join(lines),
            t.piano_card(
                title, "progression", key.name, t.scale_notes(key) if key.seven else [], voiced
            ),
        )
    ideas = _set(st, key)
    if not ideas:
        raise t.TheoryProblem(f"I don't have {st.name} progressions for {key.name}.")
    built = [(n, [t.chord_from_roman(r, key) for r in rs]) for n, rs in ideas]
    lines = [
        f"{st.name} in {key.short}{default_note}.",
        f"Style notes: {st.character}",
        "Progressions (Roman numeral = chord and notes):",
    ]
    lines += [_line(n, ch) for n, ch in built]
    first = apply_voicing(st, built[0][1])
    lines.append(
        f"Voiced for the style, first progression ({built[0][0]}), lowest to highest: "
        + " | ".join(
            f"{c.symbol} {' '.join(t.names_for(c, v.midi))}"
            for c, v in zip(built[0][1], first, strict=False)
        )
    )
    if st.bass_notes:
        lines.append(_bass(built[0][1]))
    title = f"{st.name}: {built[0][0]} in {key.short}"
    return t.Result(
        "\n".join(lines),
        t.piano_card(
            title, "progression", key.name, t.scale_notes(key) if key.seven else [], first
        ),
    )
