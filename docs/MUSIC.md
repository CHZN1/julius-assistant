# Music helpers (stage 5c-music)

Principle: Julius assists with craft, reference and busywork; the creative work stays yours. No generative image or music models, no imitating a named living artist's style, references credited.

## BPM and key (`julius/music/analysis.py`, tool `analyze_audio`)

Say: "what's the BPM of kick_loop.wav", "what key is pad_take2.wav in", "analyze this" (after dropping a file on the orb), or give a path. A bare file name is looked for in `[music] search_folders` (Downloads, Documents, Desktop by default, four levels deep); two matches make Julius ask which folder.

- Everything runs on this computer with librosa (ISC licence). Files are only read. Formats soundfile cannot decode (m4a and others) are decoded to a temporary copy with macOS `afconvert`, which is deleted afterwards.
- Files longer than 90 seconds are analysed from the middle 90 seconds. Under 3 seconds, silence and non-audio files are refused with a spoken reason.
- Tempo comes from the onset autocorrelation, refined from beat positions. Most music sits between 80 and 160 BPM, so when the half or double reading is nearly as strong and lands there it is preferred, and the other reading is listed ("or 70"). Key comes from Krumhansl-Kessler profiles against the average chroma; a low-confidence key also names the runner-up.
- Confidence is 0 to 1 and means "how clearly this beat the runner-up", not a probability.
- The card is a table: tempo, key, duration, with the confidence next to each.
- Never read, ever: Logic and GarageBand projects, `/Library/Audio`, `~/Library/Audio`, Waves, Native Instruments, Slate, Zebra, Spitfire, plug-in and Apple Loops folders (`julius/music/policy.py`, checked on the path as given and after following links).

**Accuracy is measured on synthetic clips only; accuracy on real music is unverified.** On 16 synthetic clips: key 16/16, tempo within an octave 16/16, tempo exact 10/16 (the misses are half/double-time readings). Synthetic clips are far easier than real recordings (clean chord tones, a steady click-like pulse), so treat these numbers as a sanity check, not an expected hit rate. Real-track checking was skipped by choice (no public files downloaded); if you drop labelled files of your own into `~/datasets/julius_music_eval/local/`, run the eval on them then.

Accuracy tool: `python evals/music_eval.py` (free, local) scores synthetic labelled clips, Creative Commons tracks listed in `~/datasets/julius_music_eval/manifest.json`, and your own labelled files in `~/datasets/julius_music_eval/local/manifest.json` (printed only, never saved in the repo). Manifest entries: `{"file": "x.mp3", "bpm": 120, "key": "A minor", "source": "...", "license": "..."}`.

## Theory help (`julius/music/theory.py`, tool `music_theory`, card `panel.theory`)

Ask: "what chords go with A minor", "give me a progression in G major", "what notes are in D dorian", "voicings for Cmaj9", "is Am F C G a good progression", "explain the ii V I in Bb".

- **The notes are computed, not generated.** Scales and modes (major, minor, the five other modes, harmonic and melodic minor, pentatonic, blues), diatonic triads and seventh chords, chord spelling, Roman-numeral analysis in both directions, inversions, drop 2, open and shell voicings, and smooth voice leading for a progression are plain Python (`theory.py`). Notes are spelled properly (C# in A major, Db in Ab major, Bbb in Cdim7). The model only explains what comes back, and the tool tells it to use no note names of its own.
- **Input:** a key ("A minor", "F# dorian", "Bb"), a scale or mode, a chord symbol (maj, m, dim, aug, sus2/4, 5, 6, 7, maj7, m7, m7b5 (also Dm7-5, ø), dim7, 9, 11, 13, maj9, maj13, m9, m11, m13, 6/9, add9, altered dominants 7b9, 7#9, 7#11, 7b13, 7#5, 7b5, 7alt, slash chords) or a progression as Roman numerals (needs a key) or chord names (the key is inferred, and other keys that fit are listed). Up to 16 chords.
- **Genre and era requests are answered, not declined.** "One drop reggae progression", "dub bassline chords", "neo-soul voicings for Dm9", "Motown-style ii-V", plus jazz and blues, use `styles.py`: fixed, generic conventions per genre (progressions, voicing shapes, bass notes) computed by the same theory code. With no key given it uses a default and says so. Only "in the style of" a named living artist is declined, and no artist names appear in the style data.
- **Card:** a piano with the selected chord lit (root in a second colour, note names on the keys) and a chord chart; click a chord to light it. The numbers are MIDI note numbers 21 to 108 (`docs/EVENT_FEED.md`).
- **Suggestions only.** Nothing is written to your projects, nothing is saved, and no song or artist is named or imitated: a request to write something "in the style of" a named living artist is declined.
