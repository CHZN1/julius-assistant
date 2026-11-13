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

Accuracy: `python evals/music_eval.py` (free, local) scores synthetic labelled clips, Creative Commons tracks listed in `~/datasets/julius_music_eval/manifest.json`, and your own labelled files in `~/datasets/julius_music_eval/local/manifest.json` (printed only, never saved in the repo). Manifest entries: `{"file": "x.mp3", "bpm": 120, "key": "A minor", "source": "...", "license": "..."}`.
