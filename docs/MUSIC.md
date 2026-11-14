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


## Sample index (`julius/music/library.py`, tool `find_samples`)

Ask: "find my loops around 150 BPM", "do I have a loop in F minor".

- **Only what you approve.** The index scans the folders and individual files in `[music] index_allowlist` in `julius.toml` and nothing else, ever: with no setting it scans nothing, a path outside the list is refused even if asked for, and taking an entry off the list hides it at once. A file entry approves that one file, not its neighbours (so only the CHZN1-labelled files of a bought pack can be listed, never the pack). Shipped list: the Free CHZN1 Diversity Loop Pack and New Mleodies 2 folders plus 13 individual CHZN1 files. Purchased or downloaded kits, other people's sounds and loose files are never indexed unless you add them. A sound that exists as both .mp3 and .wav shows as one entry with both formats.
- **Read-only.** It reads names, sizes and dates, never the audio, and never renames, moves, writes or deletes anything in those folders. Symlinks are not followed, and the deny-list (Logic, GarageBand, `/Library/Audio`, plug-in and sample-library folders) still applies inside approved folders and rejects an allowlist entry that is on it. The index is one small file, `~/.julius/sample_index.sqlite3`.
- **BPM and key are what the file name says** (`CHZN1_Cake_BPM160.wav`, `Dark_F#m_BPM140`). Measure the real values with `analyze_audio`.
- Separate from `search_folders`, which only finds the file you name for a BPM/key question.
- **"or" and "and".** `find_samples` takes `either`: false (default) needs every filter to match, true needs one ("F minor or about 180 BPM"). BPM matches within 3.
- **Drives that are unplugged.** An approved path on `/Volumes/...` that is not mounted keeps all its index entries, marked offline; results say "that's on <label>, which isn't connected" (`[music] volume_labels`, e.g. `{"CHZN1 1" = "your CHZN drive"}`). Entries are never dropped because a drive is missing. If the drive is mounted but macOS blocks this program, the reply names the permission (Removable Volumes or Full Disk Access). The index refreshes itself on a search when it is more than ten minutes old, and "rescan my samples", "update my sample index" or "refresh my library" rebuilds it at once (a fixed spoken command like the memory commands, no model call: `julius/music/commands.py`).
- **Drives are named by volume UUID.** Put `"volume:<UUID>/Beats"` in `index_allowlist` (the UUID is in `diskutil info /Volumes/<name>`), and label it with `volume_labels = { "<UUID>" = "your CHZN drive" }`. A different mount name for the same drive (CHZN1 vs "CHZN1 1") changes nothing; a different drive that borrows the name is not trusted. Index rows for a drive are stored as `volume:<UUID>/...`.

## Session busywork: the audio workspace and batch convert (item 4)

- **Workspace:** `~/Julius Audio Workspace` (`[audio] workspace`, also listed in `[files.folders]` as "write"). It is the only place Julius renames, moves or organizes audio, and the only place converted copies are written. "Rename kick 1.wav in the audio workspace", "move the wav files in the workspace to Drums" go through the file agent: a preview, then yes or cancel, then "undo".
- **Batch convert:** "convert all the wav files in my workspace to aiff 48k 24 bit", "convert CHZN1_Cake_BPM160.wav to mp3". wav, aiff or mp3; sample rate 22.05 to 192 kHz; bit depth 16, 24 or 32 (24 by default; mp3 is 320 kbps). It is a `convert` plan in the file agent: the preview names the new files, nothing happens before the yes, the output goes to `converted/<format>_<rate>_<depth>/` inside the workspace, an existing file is never overwritten, the originals are only read, and undo removes exactly the copies it made. Nothing is ever written to an external drive or outside the workspace.
- **Where sources may come from:** the workspace, or one file at a time from your approved sample folders (`index_allowlist`). Anything else (purchased kits, Logic, GarageBand, plug-in and `/Library/Audio` folders) is refused.
- **The audio rule outside the workspace:** more than one audio file outside the workspace in a single move, copy, rename or convert is refused ("approving a set never covers audio"); name one file, which gets its own preview and yes. Audio is never trashed by voice.
- **Converter:** macOS `afconvert` (wav and aiff) plus `lame` (mp3) are enough and need nothing installed beyond `lame`. ffmpeg is optional: when it is installed Julius uses it instead, which allows wider formats later (flac, m4a output); `brew install ffmpeg` is not required. Commands are argument lists, never shell strings.

## Drive access and the service launcher

The launchd service now starts `~/.julius/env-service/bin/python -u -m julius` directly (no `run_julius.sh` or shell in between), with the cloud-profile settings in the plist's environment, so macOS attributes file access to that interpreter. `run_julius.sh` stays for manual runs. The previous plist is kept at `~/.julius/rollback/com.julius.assistant.plist.via-run_julius.sh`.

`analyze_audio` also finds files by name in the approved sample folders (drives included), after Downloads, Documents and Desktop; a sound that exists as wav and mp3 is analysed as the wav.
