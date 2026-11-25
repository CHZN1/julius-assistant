# Julius roadmap

Updated November 24, 2026 (the next nine stages are planned under "Next stages"). Current priority order; older milestone documents (JULIUS_TEST_RESULTS.md, JULIUS_*.md) keep the historical measurements.

## Status at a glance (2026-11-09)

| Phase | State |
|---|---|
| A Evaluation harness | done |
| B Daily driver (wake word, hotkeys, service, memory, voice barge-in) | done |
| C Agents (file, coding, planner, research) | done; research agent and coding-agent network lockdown added 2026-11-09 |
| D Sports analytics | paused until the user's models are ready |
| E Open-source release | public repository prepared from a rewritten history: personal data removed, starter wake model with passive learning, `[profile]` config, README, MIT licence |
| F Voice polish and reliability | done (F1 to F3 built and live; Silero VAD skipped unless endpointing problems show; F4 soak ran 2026-11-08 to 11-09) |
| H Senses (vision on demand, presence and gestures, ShazamKit, speaker profiles, whiteboard to text) | planned, nothing built |
| G UI (orb app) | modes, typing, drops, installed app and login item built and in live testing; side-panel work (charts, images, vision, pixel art) waits for the user's go; image generation deferred (no provider) |
| Next stages 1-9 (README and repo readiness, brand, bring your own model, Apple signing, security, platform layer and ports, reliability, feedback, hosted SaaS and pricing beta) | planned 2026-11-24; stage 3 (bring your own model) built 2026-11-25 except the coding tier's own loop; brand on hold; see "Next stages" |

Tests: 849 backend unit tests; free evals (routing, file 46/46, live data 14/14, memory); paid evals (planner, coding, research, live data).

## Completed

- Diagnostics; git + private GitHub; moved out of iCloud to `~/dev/julius`.
- Cloud brain: Haiku 5.5 front end, Sonnet 5.5 planner, prompt caching, streaming with tools, daily spend cap, per-turn cost log, offline llama3.2:3b fallback, spoken auth-error message.
- Voice: MLX seed-1301 voice kept loaded, underflow-safe audio player, speech text normalization.
- Speech recognition: small.en, 0.6 s silence wait, transcript repair.
- Router shortcuts with filler stripping and follow-up carry-forward; one round trip for simple tools.
- Sonnet-controlled file agent with confirmation.
- Cloud profile is the default everywhere (`python julius.py` and `run_julius.sh` use the same defaults; `--help` prints usage).
- Since then: duplex audio with voice barge-in, UI event feed and write channel, live-data guarantees, name-to-ticker resolver, research agent, coding-agent egress allowlist (details per phase below).

## Phase A: Evaluation harness: complete (2026-10-30)

Everything lives in `evals/`; results are dated CSVs in `evals/results/` and each run reports regressions against the previous run of the same suite.

- [x] Recorded-audio regression suite (`evals/audio_regression.py`): replays the 20 recorded phrases plus the standard typed test prompts through ASR, router, brain and TTS (headless, no speakers); records transcription accuracy (WER, missed names/numbers), routing path and tool calls, first-audio latency and cost per turn.
- [x] Routing eval (`evals/routing_eval.py`, 105 cases in `evals/routing_cases.jsonl`): typed prompts labeled with the expected path (shortcut / direct answer / which tool / escalate to planner), scored automatically. A free deterministic mode (`--no-brain`) checks the shortcuts alone.
- [x] File agent eval in a sandbox folder.
- [x] A one-command eval run (`evals/run_all.py`) that runs every suite, writes the dated results CSVs and flags regressions against the last run.

First baseline (2026-10-29): routing eval 89.5% (94/105); recorded audio mean WER 8.0%, 69% of turns fully correct, median first audio 1.6 s, $0.0004 per turn. Details and the list of misses are in `evals/baselines/README.md`.

**Phase A complete (2026-10-30).** Routing 97.5-98.3% (119 cases), file agent eval 100%, scores tool handles weekdays/dates/last week, tool-turn first audio 1.66 s. ASR vocabulary hint stays on (held-out difference was one apostrophe, no hallucinated hint words; details in `evals/baselines/README.md`).

## Phase B: Daily driver for me: complete (2026-11-05)

Julius runs all day as a login service, wakes on his name, can be silenced or put to sleep without any macOS permission, and remembers what you tell him. 530 unit tests pass.

- [x] **Wake word, cascade v2** (`[wake] engine = "cascade"`): a noise-gated openWakeWord "Julius" detector (about 0.03% of a core idle, measured when built; no results file) fires at a low threshold, then one resident Whisper small.en run over the whole utterance checks the wake word (hint names Julius beside Julia/Julian/Julie/Jules) and gives the request. Numbers from `evals/results/wakecascade_*.summary.json` (`python evals/wake_eval.py suite`: wake3+wake4, 60 recordings, 3.4 min of ordinary talk, 18 held-out Julius-voice lines, verifier off):
  - **Misses: 2/60 (3.3%)** in the 14:27 and 14:34 runs (clips wake4_14 and wake4_26, both distant/quiet), the figure this phase shipped on. After the far-field fix (stream level floor 0.0045 to 0.0015, relaxed VAD precheck) the 17:34 run and a re-run at 22:14 both show **0/60**. Treat 2/60 as the honest figure until more far-field clips are recorded; 0/60 is the same 60 clips after tuning on their failure, so it is not an independent test.
  - **False wakes: 0 per hour** on 3.4 min of talk (small sample). The Whisper-only wake regression (`wake_regression.py`, 182 cases, 97.8%) has 3 false wakes and 1 miss.
  - **Julius's own voice woke it 6 of 18** in isolation (unchanged across all four runs); live, the mic gate plus the self-echo check stop this.
  - Verifier (speaker check, `~/.julius/`): the only stored run with it on (14:27-era wake3, 30 clips) had 1 miss and 0 Julius-voice wakes; it ships **off** because it is a personal layer and was not re-measured on the 60-clip set.
  - Every fire is logged to `julius_memory/wake_log.jsonl` with near-miss clips for retraining.
- [x] **Hotkeys, no permissions**: Ctrl+Option+S stops generation and speech at once (`SpeechPlayer.abort()` plus cancellation, also typed `/stop`); Ctrl+Option+J wakes from quiet mode. Both use Carbon `RegisterEventHotKey` through a small C helper (`hotkeyd/hotkeyd.c`, built on first use into `~/.julius/bin`, exits with Julius), so neither Accessibility nor Input Monitoring is needed (pynput could not be trusted under launchd). `julius_service.py doctor` checks both register. Verified live on the running service: stop mid-answer, "go to sleep" then the wake key.
- [x] **Always-on launchd service with quiet mode** (`python -m julius.service.service on|off|status|restart|wake|doctor|logs`): LaunchAgent `com.julius.assistant` (starts at login, restarts on crash only, 15 s throttle, no keys in the plist); structured JSON log `~/Library/Logs/Julius/julius.jsonl` (5 MB x 5, speech redacted unless `[service] log_transcripts`); `logs` shows recent lines and follows. "Julius, go to sleep" turns the detector fully off (persisted across restarts) until Ctrl+Option+J, `julius_service.py wake` or SIGUSR1. Still unmeasured: idle CPU/memory over a full day, restart-after-crash.
- [x] **Long-term memory** (`julius_memstore.py`, `julius_memcmd.py`, `julius_memory/memory.sqlite3`): "remember that…", "forget that / forget <topic> / forget everything", "what do you remember (about X)", "what did you just remember", "undo that", `/memories`; corrections supersede facts; automatic capture of durable facts (regex prefilter, Haiku extraction, marked auto, "Noted." once per turn; facts about Julius or secrets are never stored); forgetting hard-deletes and scrubs session logs; notes ride after the cached prompt, 400-token cap with newer facts winning, framed as data. Natural forget phrasings are caught before Haiku, a "forget that" with no target asks which one and takes the answer, and Haiku has `remember_fact` / `forget_fact` / `list_memories` tools as a backup (they only act on what the user said that turn). Eval `evals/memory_eval.py` (suite "memory" in run_all): **6/6 sections pass**: 63 command phrasings incl. negatives 100%, forgetting 0 residue, recall 100% (40 questions, 30 facts), auto-capture precision 96.2% / recall 100% (60 labelled sentences), 9 headless restart/forget/injection/tool-backup scripts all pass. Empty schema, fictional sample and README for open source in `julius_memory.example/`.
- [x] **Voice barge-in: done 2026-11-07.** Duplex audio (one 48 kHz stream, WebRTC AEC3 on a sample-aligned reference) is the default (`audio.duplex = true`); speaking over Julius stops him within about 0.3 s and the interruption is understood and answered. The earlier parked attempt (energy-based, `bargein_eval.py`) is superseded; see "Voice barge-in: DONE" in the engineering log below. Known limits: the first word of a very fast interruption is occasionally garbled; the Julia/Julian confusion applies only to the wake word.

Known open items:
- Wake verifier: built but **off**; retrain on the real-usage log and re-measure on the 60-clip set.
- Far-field wake: 2/60 misses before the level-floor fix, 0/60 after on the same clips; record a fresh far-field set to confirm.
- Julius's own voice waking him in isolation (6 of 18); live it is blocked by the mic gate and self-echo check, but a harder negative in the detector training set is the real fix.
- One auto-capture false positive in the eval ("I have to pick up the kids at four" is stored as a fact); tighten the extraction prompt on plans and errands.
- Unmeasured: service idle CPU/memory over a day, restart-after-crash, live Whisper-wake timing after sleep.
- Reminders/calendar polish.

## Phase C: Agents: COMPLETE 2026-11-06 (live voice tests passed after fixes)

Julius is the orchestrator; agents are sub-agents dispatched and supervised by the Sonnet planner. Build order:

1. **File agent hardening: built 2026-11-05** (`julius_file_agent.py`, `julius_router.py`, `evals/file_eval.py`)
   1. [x] Change preview: the spoken preview names the first files, sizes and total ("report.pdf, notes.txt and data.csv (12 MB in total) and 9 more"); the full list prints on screen; a file edited after the preview blocks the plan (re-fingerprinted at confirm); confirmation must come in a later turn.
   2. [x] Recoverable trash and persistent undo stack: "trash <file> in <folder>" / "move the pdfs in Downloads to the trash" (named files, quoted names or a file type only; "delete everything" or "delete that file" is refused with a question) moves loose files to `~/.julius/trash/<plan>/` after preview and a yes; "undo" works across restarts and repeats down a stack; "what did you change today" lists changes; "how big is the trash"; the trash is mentioned once it passes 1 GB and is emptied only by "empty the trash", which previews and then permanently deletes only what is in the trash (undo then reports those files as gone). The trash cannot be read or listed by path.
   3. [x] Planner trash proposals (`files_propose` action `trash`): staged only, every one shown in the preview and confirmed. Never proposed (enforced in the agent, also when the planner names files explicitly): folders and anything inside them (loose files only), `.app`/package bundles, music (Logic, GarageBand, Waves, NI/Kontakt, Slate, Zebra, stems, audio and plug-in file types), files beside code or project markers, source-code files, files changed or downloaded in the last 7 days, installers used in the last 30 days. Spoken voice commands skip the recency/code/installer rules (you named the file) but keep music, app and project protections.
   4. [x] Per-folder permissions: `[files.folders]` in `julius.toml` (`write` / `read` / `none`, most specific folder wins); extra folders are added by editing the file only; undo and confirm re-check them.
   5. [x] Eval: `evals/file_eval.py` now has 43 scenarios (15 original, 28 new for preview, trash, undo stack across restart, zero-files-lost invariant, empty-trash, permissions, planner protections, prompt injection), all deterministic, no model calls: 43/43; part of `run_all.py`. 41 new unit tests in `test_julius_file_hardening.py`. Not yet measured: the real planner choosing what to propose for "clean up my Downloads" (needs a paid eval).
2. **Coding agent: built 2026-11-06** (details in the Phase C part 2 section below). Decisions made: `claude -p` over the Agent SDK; $1.50 default, $5 hard max, 20-minute timeout; merging into a real project is always a separate voice yes after the diff summary; the stop hotkey stops speech only and "cancel the coding task" by voice stops a task. Original checklist, all done: 
   1. Compare the Claude Agent SDK against `claude -p` (tool control, streaming, session resume, permission model, cost reporting); pick one.
   2. Per-task cost cap enforced by Julius, with a spoken warning and a hard stop.
   3. Eval suite: small repo tasks with tests as the oracle, cost and time per task, refusal/stop behavior, no writes outside the project folder.

## Phase D: Sports analytics agent (differentiator): PAUSED until the user's models are ready (decided 2026-11-06)

Plan posted 2026-11-06 (D1 read-only summaries, D2 confirmed runner, D3 grading, D4 gated retraining, D5 notebooks); open decisions: ODDS_API_KEY handling (recommended: the user exports it, Julius never holds it), canonical NHL scripts, output layout, papermill. Do not start until the user resumes it.

Julius supervises my NHL/NFL/MLB/WNBA pipelines in `~/dev` as sub-agents: run today's predictions, summarize results and parlays, report model performance, trigger retraining, all with confirmation before anything writes or bets.

## Phase E: Open-source release (portfolio)

- Audit the full git history for secrets and personal data (API keys, .env, julius_memory, planner state, my recorded voice phrases); report findings before any history rewrite.
- Bring-your-own Anthropic key via `.env.example`; clear setup README with requirements (Apple Silicon, memory), a demo GIF/video, architecture diagram, and cost-per-turn table.
- **Never ship local voice data:** `voice_data/` (eval recordings of the user's voice and conversations), `julius_memory/`, `~/.julius/` and the personal wake verifier stay out of the repo and out of any release archive. `python evals/voice_record.py delete-all` removes every recording.
- MIT license.
- Wake word: the shipped detector (`julius/wake/models/julius_starter.onnx`) and the default `engine = "cascade"` path must work for anyone without the personal speaker verifier. The verifier is a personal layer trained on one person's voice (`python -m julius.wake.verifier train`) and stored outside the repo in `~/.julius/`; the repo must not contain recordings, the verifier file or `julius_memory/wake_log.jsonl`. The detector was trained with the author's wake recordings as part of its positives: before release, retrain it from synthetic voices only (or ship it clearly labelled) and document how to record a personal wake set. Confirm the openWakeWord models download step (`openwakeword.utils.download_models`) is in the setup README.

## Phase F: Voice polish and reliability: DONE (built 2026-11-06 to 11-08; Silero VAD skipped)

Goal: voice conversations feel as reliable as typed ones. Text tests pass; the remaining failures are in the voice layer (ASR, echo, endpointing, pending state, Haiku improvising).
1. **Diagnose** from the logs and the conversation record: every voice failure of the last week, grouped by cause and counted (done 2026-11-06, see "Failure breakdown" below).
2. **Multi-turn voice eval:** a script that prompts the user to record about 10 realistic multi-turn conversations (follow-ups, yes/no confirmations, "no, I meant..." corrections, a pause mid-sentence, speaking while Julius talks), replays them through the full pipeline and scores per turn: transcript accuracy, correct handling, latency. Added to `run_all.py`.
3. **Fixes, in the user's order, each measured on the eval:** (a) always-on WebRTC echo cancellation on the mic input (reference = what Julius plays), even with voice barge-in off: the biggest failure category (12 cases, 4 answered); (b) natural confirmations ("yeah", "sure", "go for it", "nah") shared by file, coding and memory sessions; (c) low-confidence handling: confirm ("Did you say report.txt?") instead of guessing, optionally re-run a larger Whisper on those utterances; (d) a short spoken acknowledgment ("on it") before anything slower than about 1.5 s; (e) Silero VAD endpointing instead of energy plus a fixed 0.6 s, with a longer wait when the sentence sounds unfinished; (f) **no made-up live data:** Haiku never states scores, prices, weather or schedules that did not come from a tool result in that same turn; if the tool fails or was not called it says it could not get it (eval cases: s09 plus routing cases); (g) **undo across agents:** "undo" applies to the most recent action of any agent (file, memory, coding merge) and says which one it undid (eval cases in routing and s03/s04/s08).
4. **Reliability:** a 24-hour soak test of the service (CPU, memory, crashes and restarts, every error and traceback) plus a log scanner that summarizes errors; fix what it finds.

**Failure breakdown (data window: service log 2026-11-05 20:23 to 11-06 18:19, conversation record 11-05 16:37 to 11-06 18:14, 95 turns; typed headless test turns excluded; only these two days exist for the last week).**
| Cause | Count | Examples |
|---|---|---|
| ASR dropped or garbled words | 8 (3 harmless) | "Thursday is the season of 45", "you won the NHL last night" for "who won", "Aans, a", glued "JuliusTrashReport.txt", one utterance with no transcript |
| Self-hearing echo | 12 (4 answered, 7 caught by the filter, 1 real "yes" wrongly dropped) | "This note is gone", "14 hours ahead of you", an 18-word garble of his own preview |
| Endpointing / truncation (suspected, audio not saved) | 3 | "TrashReport." then "Oh, report.txt." three times |
| Pending-state confusion | 5 | bare filename after a clarification, "yes" after a status question, "undo" taken by the memory handler, "yes" after a garbled turn |
| Haiku improvising or false "I can't" | 5 (+2 caught by the stall guard) | fabricated NHL scores, "I can't delete notes", "I can't move files myself", a status request "not understood" |
| Infrastructure (not the voice model) | 6 TTS outages, 3 hotkey-permission failures, 2 underflow bursts (from my own eval load) | speech tmp dir deleted by macOS (fixed), service Python lacks Accessibility for hotkeys |
Not measurable today: turn latency (not logged), real endpointing behaviour (no saved audio), anything older than 2 days.

**F1 status (2026-11-06):** built and live: `julius_logscan.py` (error and drop summary by cause, latency percentiles, wake detector stats), `julius_soak.py` (read-only sampler as its own launchd agent, baseline started 2026-11-06 19:06; `report` summarizes), a `[timing: ...]` line for every turn in the service log (speech end to first audio, ASR seconds and log-probability, no-speech probability, routed or brain, errors). Findings: the 3 hotkey failures were all before the Carbon fix (last at 20:38 on 11-05; fix 21:13), none since, so no Accessibility permission is needed. Of 387 rejected wake fires, Whisper spent 146 s in total (about 0.2% of one core over 22 hours); 2 were real wakes that were rejected because the wake gate did not accept a glued wake word ("JuliusTrashReport."), fixed with tests; about 3 more were possible quiet wakes with an empty Whisper result (cannot be confirmed from the log).
**F2 status:** recorder built (`julius_session_record.py`, enabled only by `JULIUS_SESSION_RECORD_DIR`) and the recording script `evals/voice_record.py` with ten scenarios (`evals/voice_scenarios.json`). Recordings live in `voice_data/` (git-ignored, never released, `delete-all` command). The replay and scoring harness is built after the user records.

## Phase G: UI (started 2026-11-08)

The UI lives in its own repo, split out on 2026-11-09 with its history. This repo keeps only the interface: the read-only event feed (`julius_events.py`, `docs/EVENT_FEED.md`, `julius_events_client.py`, a test that the document matches the code) and the authenticated write channel (`julius_uichannel.py`, `julius_dropstage.py`, `docs/UI_CHANNEL.md`: typed text and dropped files; dropped files are only staged, nothing is opened or moved without the file agent's own yes).

Built: the animated orb (states asleep/quiet/offline/listening/thinking/speaking, 8 finishes, shake and brush physics, power caps, settings drawer), window modes (Mini 100 px with corner snap, Floating 420 wide, Full), typing in the orb, drag and drop onto the orb, click-and-drag to move, restart sync, `Julius Orb.app` in `~/Applications` with menu-bar Quit and Cmd+Q, and a login item (`julius_orb.py on`). Details and tests are in that repo's README.

**Stage 5 status.** 5a (charts, tables, image galleries) done 2026-11-11. **5b (vision) done 2026-11-12:** "what am I holding / what's on my screen / look at this image" take one camera frame (one-shot AVFoundation helper, indicator on only during capture), the frontmost non-Julius window, the whole screen ("whole screen") or a dropped image; 1568 px long edge; Haiku, Sonnet for "look closely" / "read this"; locked screens and secure password fields refused; the picture is held in memory only and reaches disk only through "save this card"; each look shows its cost on a `panel.vision` card. Eval (`evals/vision_eval.py`, paid): 7 of 7 runs correct on six drawn pictures (reading text, colours and shapes, counting, a settings window, a blank picture that must not be invented into, and text in the picture giving orders, which was described, not obeyed), about $0.0006 per look on average ($0.0034 for Sonnet reading text). Live check by the user (screen, dropped image, camera with a tennis ball): passed, 0 files written to the panel store. Also done since: research cites only pages it opened, "research / find sources on / search the web for" always go to the research agent and "look up" does unless a dedicated tool fits, up to 3 tool rounds per turn with a $0.15 per-turn cap, and the coding-agent test race fixed.

Creative principle (2026-11-13): Julius assists with craft, reference and busywork; the creative work stays the user's. No generative image or music models, no imitating a named living artist's style, references credited. **Image generation is intentionally not planned** (this replaces the earlier "5d, deferred, no provider yet").

**5c-music (approved 2026-11-13), remaining order (updated 2026-11-13): (3) theory help; (2) sample library index; (4) session busywork (batch convert to NEW copies, rename/organize only inside `~/Julius Audio Workspace`), each stopping for review.** Item 1 (BPM and key) is done. Never indexed or touched: Logic, GarageBand, `/Library/Audio`, Waves, NI, Slate, Zebra, Spitfire and plug-in folders (`julius/music/policy.py`). `ffmpeg` is approved for item 4 only, via Homebrew, after the exact command is shown.

**Item 5: Song identification via ShazamKit; deferred until the user has an Apple Developer account.** AcoustID/Chromaprint was evaluated and skipped (weak coverage of rare/older tracks and noisy mic capture); `chromaprint` is not installed and nothing was built for it. Unofficial Shazam wrappers (ShazamIO and similar) are excluded because they violate Shazam's terms. **Acceptance test, kept for when this resumes (required before song ID counts as done):** about 40 songs chosen by the user in four tiers (current hits, older classics, less popular/small artists, rare/older reggae), each captured two ways (clean from a local file or system audio; real-world through speakers and the mic from across the room, 10 s); report hit rate per tier and condition plus wrong matches; Julius says "I couldn't identify it" without a confident match and never guesses; if rare reggae or the mic condition scores poorly, stop and lay out the options (ACRCloud or AudD, with current pricing, terms and free tiers from their official sites), signing up for nothing.

**5c-music item 1 (BPM and key) built 2026-11-13** (`julius/music/analysis.py`, `analyze_audio` tool, result as a `panel.table` card; a dedicated audio card can come with the theory card). librosa (ISC) for tempo and chroma, Krumhansl-Kessler key profiles, nothing leaves the machine, files are read-only. Accuracy (`evals/music_eval.py`): on 16 synthetic labelled clips (drums at a known tempo under chords in a known key) key 16/16, tempo within an octave 16/16, tempo exact (within 2%) 10/16: the misses are half/double-time readings (below 80 BPM or above 160 the tool prefers the 80-160 reading and lists the other as an alternative). Synthetic clips are easy for key detection. **Real-music tempo measured 2026-11-14** on 200 of the user's own files labelled by the BPM in their names (`evals/music_filename_eval.py`; data local only): exact 50% before, 83.5% after (held-out half 49% to 87%) once tempo scoring changed to a harmonic comb with a 130 BPM prior and a 3:2/4:3 beat-grid check; drum-only loops now report "no clear key"; a filename BPM is shown beside the measured one. Real-music key accuracy is still unmeasured.

**Art tools (after music; deterministic image processing only, no generative models; redefines the old "5c pixel art").** Photo to pixel art (downscale, palette quantization, optional outlines, chosen sizes and palettes); a style filter set for photos: cel-shade/cartoon (colour quantization, bilateral smoothing, ink edges), comic ink with halftone, pencil sketch, posterize/pop art, watercolor-ish, each with a few adjustable settings (line weight, number of colours, smoothness) and a before/after view in the panel; a drawing digitizer (cleanup, line extraction, flat colour fill with the user's chosen colours); a "tracing guide" mode (photo to clean line art to draw over, feeding the digitizer); logo to SVG vectorizer (potrace or vtracer, licence check first); a pixel grid editor in the panel. **Intentionally excluded:** anime/cartoon GAN or diffusion style-transfer models (trained on studio films without permission) and any generative image model.

Left in Phase G: 5c-music (items 3, 2, 4; item 5 deferred), then the art tools above; switchable panel views (live chart or sports slate as card plugins). (The Springfield routing coin flip is fixed: the ambiguous-city cases r049, r072 and r118 accept either a weather call or a clarifying question via `clarify_ok`, and they pass in the routing evals.)

Side panel: built (in the orb repo). Attached, split and detached layouts, switchable from the panel header or the Panel menu and remembered. Cards built: research summaries with source chips, file plans and listings, chart, table and image gallery, vision, and theory (drawn piano and chord chart). Still to come: **switchable panel views**. The panel is a host for card and view types registered in one place, so a live chart or a sports slate can be added as a plugin without changing the panel shell.

Note on `native/apple/bridge.js`: it is not UI. It is the JavaScript-for-Automation bridge that `julius_apple.py` pipes JSON into (via `osascript`) to read Calendar and Reminders inventories and to create, complete and cancel tokenised events and reminders.

## Phase H: Senses (later; plan only, nothing built)

Julius gets eyes, presence and more ears, always on-device where it can be and always confirmed per use when anything goes to the cloud.
1. **Vision on demand.** "What am I holding?", "read this", "what's on my screen?": one camera snapshot or screenshot sent to Claude vision, confirmed per use (spoken or on screen), never continuous video to the cloud. Builds on the side-panel image path and the research agent's image results.
2. **Local, low-power presence and gestures** with Apple's Vision framework (on-device, no frames stored or sent): wake on sit-down, go quiet when you leave, thumbs-up = yes, raised hand = stop. Frame-rate and CPU budget measured like the wake word (idle cost must stay near zero); off by default; a visible indicator whenever the camera is in use.
3. **Song recognition** with ShazamKit ("what's playing?"), on request only.
4. **Speaker recognition** for per-person profiles and memory, reusing the speaker-verifier work (`wake/verifier.py`) and the `[profile]` section: who is talking decides name, memory namespace and permissions; unknown voices get the guest profile.
5. **Whiteboard and handwritten notes to code, tasks or text**: a snapshot (confirmed per use) turned into a checklist, reminders, a code file proposal (staged through the coding agent, never written directly) or plain text.
Open design points: camera and screen-recording permissions flow, the consent UI in the orb, retention (nothing stored by default), and how per-person memory is kept apart on disk.

## Next stages (plan, 2026-11-24; nothing below is built)

Nine stages, in order. Effort is in focused weeks for one developer working with the coding agent **[A]**. Anything marked **[A]** is an assumption, not a measurement.

**Pricing:** Free tier: open source, bring your own key or local models. Hosted plan: planned, after a free beta.

| # | Stage | Effort | Depends on |
|---|---|---|---|
| 1 | README and repository ready for visitors | 1 wk | nothing |
| 2 | Brand: JULIUS wordmark | 1 wk once the maintainer's sketch exists | the maintainer's letter sketches |
| 3 | Bring your own model | built 2026-11-25 (coding tier's own loop: planned, about 1.5 wk) | nothing |
| 4 | Apple Developer Program: signing, notarization, ShazamKit | 1 wk plus Apple's waiting time | nothing; needed before any download is offered |
| 5 | Security | 3-4 wk, plus an outside pentest | the threat model starts now; the pentest needs 9 |
| 6 | Platform layer, then Windows and Linux ports | 2 wk layer, then 4-6 wk per OS | 3 (local models make the ports usable without an API key) |
| 7 | Reliability: load, soak, release evals | 2 wk, then ongoing | 9 for load tests; evals exist |
| 8 | Feedback in the app | 1-2 wk | the orb app; 9 for the server half |
| 9 | Hosted SaaS and pricing beta | 6-8 wk, plus legal review | 4, 5 (threat model and pentest), a free beta before any charge |

### Stage 1: README and repository ready for visitors

**Goals.** Someone who arrives from the demo video understands Julius in a minute, can install it on a Mac by following the README alone, and can find a first thing to contribute.

**Work.** The demo at the top of the README; a one-paragraph pitch; a feature list that matches the video; macOS install steps (requirements, API key setup, permissions); the bring-your-own-models note; links to this roadmap and the licence; a CI badge; a note that the orb (UI) app is not open source yet. `CONTRIBUTING.md` (tests, evals, code style, how to propose a tool), issue templates (bug report, feature request), and 3-5 `good first issue` items taken from this roadmap.

**Acceptance tests.** A fresh clone on a Mac, following only the README, installs and runs Julius in text mode, and the unit tests pass. Every link in the README resolves. The badge shows the real status of the latest run on `main`.

### Stage 2: Brand, the JULIUS wordmark

**Goals.** A custom JULIUS wordmark drawn from the maintainer's own letters, used everywhere the name appears.

**Work.**
1. The maintainer sketches the six letters on paper, with several tries of any letter they are unsure of.
2. Julius digitizes them: a photo or scan goes through the art helpers (threshold, clean-up), then a trace to vector (potrace or similar).
3. The outlines are hand-fixed: consistent stroke weight, the baseline and x-height aligned, the spacing kerned.
4. Outputs:
   - an SVG master in `brand/` (one colour, plus a gold version on dark);
   - the app icon updated in julius-orb (the `.icns` set from 16 to 1024 px);
   - the favicon;
   - social profile pictures (square, 400 and 800 px);
   - banners: GitHub social preview 1280×640, YouTube 2560×1440 within the safe area, LinkedIn 1584×396, X 1500×500;
   - a one-page usage sheet with clear space, minimum size and colours.

**Acceptance tests.**
- The SVG master opens cleanly in a browser and in Inkscape, and has no stray points (node count checked).
- The wordmark reads at 16 px and on a phone at banner size.
- Every export matches its platform's size.
- The app icon passes the Stage 4 signing build.
- The maintainer approves the final letters.

### Stage 3: Bring your own model

**Status (2026-11-25): built**, apart from the coding tier's own edit/test loop (planned in `docs/PROVIDERS.md`, about 1.5 weeks, so not built yet; `claude -p` stays). Adapters for Anthropic, OpenAI-compatible endpoints and Ollama native; per-tier `[models.<tier>]` config with old configs unchanged; provider and model on every cost-ledger row; `run_all.py --provider` with the floors below. The default Anthropic setup showed no regressions (routing 267/267 ×3, shake 46/46). A small local model (qwen3 4B) is below the floors; the feature is meant for large self-hosted or hosted open models. Measured rows: `docs/PROVIDERS.md`.

**Goals.** Every model tier can be swapped through one documented provider interface: the voice front end (`[models] haiku`), the planner (`[models] sonnet`), the research sub-agent, and the coding agent (`[code] model`, currently `claude -p`). Supported providers: Anthropic, any OpenAI-compatible endpoint (which covers LM Studio, llama.cpp `llama-server`, vLLM, OpenRouter and Ollama's `/v1`), and Ollama native. The offline fallback (`llama3.2:3b` through `OLLAMA_FALLBACK` in `julius/brain/backend.py`, plus `[brain] mode = "local"` with `qwen3:4b-instruct`) is the starting point: it already speaks the Ollama tool-call format.

**Work.**
- A `Provider` protocol in `julius/brain/providers/`. It takes `chat(messages, tools, stream, max_tokens)` and returns normalized events: text deltas, tool calls, usage and stop reason. It also declares capabilities: tool calling, vision, prompt caching, context size, and a price table for the cost ledger (local models cost $0).
- `CloudBrain` becomes the `anthropic` adapter; its `convert_tools` and `convert_messages` move behind the protocol. New adapters: `openai_compat` and `ollama`.
- Per-tier config replaces the two hard-coded keys. Each tier gets its own `[models.front_end]`, `[models.planner]`, `[models.research]` and `[models.coding]` block with `provider`, `model`, `base_url` and `api_key_env`; old configs keep working.
- Coding tier. `claude -p` stays the default. A non-Anthropic coding model needs Julius's own edit/test loop (Read/Edit/Bash in the same sandbox and egress allowlist) so the safety rules do not depend on one vendor's CLI.
- Docs: `docs/PROVIDERS.md` covers setting up each provider, a capability matrix, the known weak spots of small local models (tool-call reliability, long prompts), and the tier mix each one is measured to work for.

**Acceptance tests.**
- `python evals/run_all.py --provider <name> --tier <tier>` runs the routing, file, live-data, memory, planner and research evals against any configured provider and writes the same dated CSVs.
- Each eval report gains a provider column, and a provider passes a tier only at the floors below.

| Floor | Value |
|---|---|
| Routing | ≥ 95% (the cloud baseline is 97.5-98.3%) |
| File agent | 100% (no unsafe action) |
| Tool-call JSON validity | ≥ 99% |
| Memory eval | 6/6 |

- A full turn works with no Anthropic key at all: the front end and planner on LM Studio or Ollama, recorded in the eval CSVs.
- The cost ledger records a provider and model on every row, and local rows record $0.
- Unit tests feed the same recorded tool-call conversations through every adapter and get identical normalized events.

### Stage 4: Apple Developer Program ($99 a year)

**Goals.** Anyone can download and open Julius on a Mac without the "cannot be opened" or "unidentified developer" warning, and the program's services that need membership become available. This has to happen before the app is given to anyone.

| What it unlocks | What it needs |
|---|---|
| **Developer ID signing**, so Gatekeeper accepts a downloaded app | Membership; a Developer ID Application certificate in the keychain; the orb app built with the hardened runtime and a secure timestamp, without the `get-task-allow` entitlement; the bundled helpers (`hotkeyd`, `visiond`, the Python runtime) signed one by one, inside out |
| **Notarization**, Apple's automated malware check, stapled to the app | `xcrun notarytool submit --wait` with an app-specific password or an App Store Connect API key, then `xcrun stapler staple`; it has to be redone for every release, so it belongs in the release script |
| **Developer ID installer** (a signed `.pkg`), optional | A Developer ID Installer certificate; useful if the Python backend is installed outside the app bundle |
| **ShazamKit song ID** ("what's playing?") | An App ID with the ShazamKit App Service enabled in Certificates, Identifiers & Profiles, and the entitlement in the provisioning profile. The acceptance test is already saved (every tier, a clean recording and through the mic, no guessing); AcoustID stays skipped. Forum reports show macOS-specific problems (the entitlement rejected at upload, error 202 under the App Sandbox), so budget time for this and test the non-sandboxed Developer ID build first **[A]** |
| **WeatherKit**, optional | Membership plus the WeatherKit capability; Apple's documentation lists it among the capabilities that Developer ID apps cannot use, so check that first; otherwise keep the current weather source |
| **Mac App Store**, not planned | The App Sandbox, which conflicts with the file agent, the coding agent's sandbox and global hotkeys; Developer ID distribution is the plan |
| **TestFlight for macOS**, optional | App Store Connect and a sandboxed build; skip unless a Store build happens |

**Acceptance tests.**
- On a clean Mac (a new user account, the quarantine flag kept by downloading through Safari), the app downloaded from the release page opens with no Gatekeeper warning.
- `spctl --assess --type execute -vv` reports "source=Notarized Developer ID".
- `codesign --verify --deep --strict` passes on every nested binary.
- `stapler validate` passes.
- The release script fails if notarization fails.
- ShazamKit passes the saved acceptance test (every tier, clean and through the mic).

### Stage 5: Security

**Goals.** A written threat model, and tests that prove the defences hold, before any paid launch.

**Work and acceptance tests.**
- **Threat model** (`docs/THREAT_MODEL.md`). The assets are:
  - files on the Mac;
  - the API key and device tokens;
  - memory;
  - the camera, mic and screen;
  - money, through model spend and Stripe.

  It covers the entry points (the voice, typed input, the orb's local ports, web pages the research agent reads, files dropped or read, images and the screen sent to vision, the coding agent's project copies, the hosted proxy and webhooks) and the trust boundaries. It is reviewed whenever a new tool or entry point is added.
- **Prompt-injection suite** (`evals/injection_eval.py`). Hostile instructions are hidden in:
  - web pages fetched by research;
  - files the file agent reads;
  - text in images and on the screen sent to vision;
  - memory facts;
  - a project the coding agent works on.

  Pass: 0 unconfirmed side effects across the whole suite (no file moved, no message, no network call outside the allowlist, no memory written without the user saying so), and every injected instruction is either ignored or reported back to the user as text. It runs in the release evals.
- **Coding-agent sandbox escape tests.** Attempts to:
  - write outside the project copy;
  - read `~/.ssh` or `.env`;
  - reach a host outside the egress allowlist;
  - start a background process that outlives the task;
  - touch the real repo or push.

  Pass: every attempt is blocked and logged. It runs on every platform's `Sandbox` from Stage 6.
- **Local ports 8765 (event feed), 8775 (UI channel) and 8785 (panel).**
  - **Binding and authentication:** all three are bound to 127.0.0.1 only. Every request needs the per-session token; check the Origin header against DNS-rebinding pages.
  - **Fuzzing:** run the JSON and WebSocket handlers for 24 hours with no crash or hang.
  - **Pass:** a web page in a normal browser can't drive Julius, which is tested with a rebinding page.
- **Dependency and secret scanning in CI.**
  - **Dependency scanning:** `pip-audit` (or GitHub's Dependabot alerts) on julius-assistant. The build fails on known high-severity vulnerabilities.
  - **Secret scanning:** gitleaks on every push, plus GitHub's own secret scanning. GitHub's is free for a public repo; for the private orb repo, run gitleaks on the self-hosted runner.
- **Rate limits and abuse protection (hosted):**
  - per user and per IP limits on sign-in, Checkout and the proxy;
  - device-token revocation;
  - a blocklist for payment fraud, using Stripe Radar defaults;
  - Anthropic's Usage Policy enforced through our terms, with abuse reports acted on within 24 h.
- **An outside pentest before the paid launch.** The scope is the hosted service, the Mac app's local ports, and the install and update path. Every high finding must be fixed and re-tested before charging.

### Stage 6: Platform layer, then Windows and Linux ports

**Goals.** Every Mac-only part sits behind an interface in `julius/platform/`, chosen at startup. The macOS build keeps working unchanged, and each port is a new set of implementations, not a fork.

| Mac-only part today | Where | Interface | Windows | Linux |
|---|---|---|---|---|
| MLX Whisper (speech recognition) | `runtime/asr.py`, `audio/asr_resources.py` | `SpeechToText` | faster-whisper (CUDA or CPU) or whisper.cpp | the same |
| MLX Qwen3-TTS voice worker | `audio/mlx_tts_worker.py`, `mlx_voice_support.py` | `TextToSpeech` | the same Qwen3-TTS weights through PyTorch or ONNX on CUDA; a CPU fallback voice (Kokoro or Piper) **[A]**; the voice needs re-tuning | the same |
| MLX local brain | `brain/backend.py` (`mlx`) | Stage 3 provider | Ollama or llama.cpp | the same |
| Apple Vision OCR and screen capture | `visiond/visiond.m`, `agents/vision.py` (`screencapture`) | `ScreenReader`, `OCR` | Windows.Graphics.Capture plus Windows.Media.Ocr | xdg-desktop-portal screenshot plus Tesseract or PaddleOCR |
| launchd LaunchAgent | `service/service.py`, soak tools | `ServiceManager` | a Task Scheduler task at logon | a `systemd --user` unit |
| Carbon hotkeys | `hotkeyd/hotkeyd.c`, `service/hotkey.py` | `GlobalHotkeys` | `RegisterHotKey` | X11 `XGrabKey`; on Wayland, the GlobalShortcuts portal where the desktop supports it, otherwise a documented manual binding |
| afconvert | `music/convert.py`, `music/analysis.py` | `AudioConvert` | ffmpeg | ffmpeg |
| osascript plus the JXA bridge for Calendar and Reminders | `brain/apple.py`, `native/apple/bridge.js` | `CalendarBackend`, `RemindersBackend` | Microsoft Graph (Outlook) or CalDAV | CalDAV (GNOME Online Accounts or Evolution) |
| `sandbox-exec` for the coding agent | `agents/code_agent.py` | `Sandbox` | AppContainer or WSL2; Docker as a fallback | bubblewrap plus a network namespace for the egress allowlist |
| Swift orb shell (WKWebView, AVFoundation camera) | julius-orb `app/` | the page already talks to the backend over 8765/8775/8785 | Tauri on WebView2, with the camera through `getUserMedia` **[A]** | Tauri on WebKitGTK |

Already cross-platform: duplex audio through PortAudio, WebRTC AEC3, openWakeWord, the evals, and the memory store (SQLite).

**Acceptance tests.**
- On macOS, the whole unit suite and the free evals give the same results before and after the layer goes in (no behaviour change).
- An import test fails if any module outside `julius/platform/mac/` imports MLX, PyObjC or calls `osascript`, `launchctl` or `afconvert`.
- Per port:
  - CI runs the unit suite on `windows-latest` and `ubuntu-latest`. julius-assistant is public, so those runs are free.
  - The routing and memory evals pass at the macOS figures.
  - The wake-word suite runs on the 60 recorded clips: misses ≤ 3/60, and 0 false wakes on the talk set.
  - Speech recognition on the 20 recorded phrases: WER within 1 point of the Mac.
  - Time to first audio ≤ 2.5 s on the reference machine **[A]**.
  - The service starts at login and restarts after a crash.
  - The hotkeys stop speech within 0.3 s.
  - A sandbox escape test (Stage 5) passes.

### Stage 7: Reliability

**Goals.** Known limits under load, and no silent regressions between releases.

**Work and acceptance tests.**
- **Load test (hosted).** A scripted mix from the ledger's request shape, using a mock model with the real latency distribution so the test doesn't spend money.
  - At 10× the expected peak (start at 50 concurrent users, 5 requests a minute each **[A]**), the proxy adds ≤ 150 ms at p95 and errors stay < 0.5%.
  - Caps are still exact under concurrency: no user exceeds the cap by more than one turn.
- **Stress test.** Ramp until it fails. It must fail closed (requests refused with a clear message), never let calls through unmetered, and recover by itself when the load drops.
- **Soak test.**
  - **Hosted:** 72 h at the expected load with no memory growth over 10% and no error-rate drift.
  - **Local:** the existing `service/soak.py` and `postsoak.py` extended to 7 days of always-on listening, with idle CPU, memory, wake misses and restarts recorded. It passes at today's daily-driver figures (the Phase B open item: idle CPU and memory over a full day was never measured).
- **Regression evals on every release.**
  - The release script runs `evals/run_all.py` (free suites always; paid suites under a per-run spending cap).
  - The release is blocked on any regression against the last release's CSVs: routing −1 point, file agent below 100%, memory below 6/6, the planner and research scores, and voice time to first audio +20%.
  - The results are attached to the GitHub release.

### Stage 8: Feedback in the app

**Goals.** Users can tell us what works and what breaks without leaving the app, and nothing is collected without their say-so.

**Work.**
- **Thumbs:** a thumbs up or down on every answer and card in the orb panel. One tap sends a rating with only the turn's kind (for example "research card", "files: move") and the app version.
- **"Report a problem"** in the orb menu opens a form: what happened, what was expected, and an **unticked** "include diagnostics" box. The box shows exactly what would be sent before sending: recent log lines (already redacted unless `log_transcripts`), the config with secrets removed, versions and the last error. Transcripts or screenshots are a separate, unticked box.
- **Consent:** off by default. The first prompt asks once, and the choice can be changed in settings. Free users' feedback goes to a GitHub issue draft, opened in the browser for them to submit, or to our endpoint if they opt in. Hosted users' feedback goes to our endpoint.
- **Retention:** diagnostics are kept 90 days, then deleted; a user can ask for theirs to be deleted.

**Acceptance tests.**
- With feedback off, a network capture of a full session shows no feedback or diagnostics traffic.
- Unit tests show the payload exactly matches what the preview showed.
- Secrets in `.env` and API keys never appear in a diagnostics bundle (a test that plants fake keys).
- The orb's UI tests cover the thumbs and the form.

### Stage 9: Hosted SaaS and pricing beta

**Goals.** A user signs up, pays and uses Julius without an API key. The app on their Mac talks to our proxy, we meter and limit every model call, and nothing about their payment card ever reaches our servers.

**Work.**
- **Free beta first.** A free beta of the hosted plan with usage limits, to measure real use before any price is set. The free tier (open source, bring your own key or local models) stays as it is.
- **Account service.** Email sign-in with a magic link or passkey, no passwords stored. Device tokens for the Mac app: short-lived, refreshed, revocable per device. One small service (Python FastAPI or similar **[A]**) on Postgres.
- **Model proxy.** The Stage 3 `anthropic` adapter pointed at our endpoint. It checks the device token, meters input and output tokens per call from the provider's usage fields, and applies the plan's limits before each call. Our Anthropic key lives only on the server.
- **Usage metering and limits.** A ledger table (the `costs.jsonl` row shape plus a user id). Caps are checked in a transaction. The user sees their usage in the orb's settings.
- **Payments.**
  - Stripe Checkout (hosted by Stripe) for the first payment.
  - Stripe Billing for subscriptions and the yearly plan.
  - The Stripe Customer Portal for cancelling and changing cards.
  - Card data never touches our servers (PCI SAQ A scope).
  - Webhooks: the `Stripe-Signature` header is verified with the endpoint secret before anything is processed. Events are idempotent (stored event ids), and the subscription state is re-read from the Stripe API, not trusted from the event body.
  - `customer.subscription.updated` and `.deleted`, plus `invoice.payment_failed`, drive access, with a 3-day grace period **[A]**.
- **Deployment.** One region at the start, with infrastructure as code. Secrets in the platform's secret store, never in the repo. Staging and production are kept apart, with Stripe test mode on staging.
- **Monitoring.** Uptime checks, error tracking, a dashboard of spend per hour against the global ceiling, and an alert when the proxy error rate passes 2% or p95 latency passes 1 s on top of the model's own time.
- **Backups.** Daily Postgres snapshots kept for 30 days, a monthly test restore, and point-in-time recovery if the host offers it.
- **Legal, before charging anyone.**
  - Terms of service and a privacy policy (what the proxy logs and for how long; that memory stays on the Mac), reviewed by a lawyer.
  - A data processing addendum if we have EU users.
  - **Anthropic's terms:** the Commercial Terms (effective 2025-06-17) let customers use the API to power products for their own end users. They also require:
    - that we and our users follow the Usage Policy, and use the API only in supported countries and regions;
    - that our users are told to check outputs for accuracy before relying on them;
    - that we don't use the API to build a competing model.
  - Anthropic does not train on API customer content.
  - **To check before launch:** the coding agent currently runs the `claude` CLI. On the hosted plan it must authenticate with our API key (or move to Julius's own loop from Stage 3), never with a personal Claude subscription login, since consumer plans are under the Consumer Terms and are not for powering a product. Confirm that and the region list on the live terms page.

**Acceptance tests.**
- **End to end on staging, in Stripe test mode:** sign up, Checkout with a test card, then a turn through the proxy. Then cancel in the portal and confirm the proxy refuses further turns.
- **Webhooks:** a tampered signature is rejected (400), and a replayed event is processed once.
- **Limits:** a scripted user who hits the monthly cap is moved to the front end only on the call that crosses it, and a user over the cap is never charged extra.
- **No card data:** a check of every log, table and request body finds no card number or CVC.
- **Recovery:** a staging database restored from last night's backup comes up and passes the end-to-end test.
- **Load:** the Stage 7 load test passes on staging.

### Sources (checked 2026-11-24)

- Apple:
  - Membership and capabilities: [compare memberships](https://developer.apple.com/support/compare-memberships), [what's included](https://developer.apple.com/programs/whats-included/).
  - ShazamKit setup: [ShazamKit App Service](https://developer.apple.com/help/account/configure-app-services/shazamkit).
  - Known macOS problems: [entitlement rejected on macOS](https://developer.apple.com/forums/thread/816377), [error 202 under the App Sandbox](https://developer.apple.com/forums/thread/837008).
- Anthropic: [Commercial Terms of Service](https://www.anthropic.com/legal/commercial-terms).

## Later

Vision branch (beyond emotion detection). The hosted version is now planned as Stage 9 above.


## Engineering log (dated notes, newest findings last)

### Planner eval (paid), 2026-11-06
`evals/planner_eval.py` (in `run_all.py` as "planner (paid)"): real Sonnet planner, generated Downloads shaped like the user's, 5 requests x 5 runs, about $0.27 a run of 25. Result: 25/25 runs with no protected or recent item staged and the folder unchanged; 100% of runs proposed something; 70% of the old installers and duplicates were found on average; no overreach on datasets or archives; the planner itself named a protected item (an installer opened recently or downloaded this week, a new screenshot) in 6 runs, which the agent blocked. Cautious misses: Zoom.pkg missed in 22 runs, old screenshots in 10-14.


### Phase C part 2: Opus coding agent (built 2026-11-06)
`claude -p` (Opus 5.5, `--bare`, `dontAsk`) run by `julius_code_agent.py` / `julius_code_session.py`, configured in `[code]`.
- **Sandbox:** the project is COPIED (no secrets, caches, symlinks, music or crypto names, no git history) to `~/Library/Application Support/Julius/code/<id>/work` with a fresh git repo that has no remote. The process runs under a macOS seatbelt profile: writes only inside the task folder, no reads of `~/dev`, Documents, Desktop, Downloads, Music, keys or Julius state, network only through a local allowlist proxy that reaches api.anthropic.com and nothing else (2026-11-09). Environment is scrubbed (only the one API key). Tools are an allow-list; `git push`, remotes, curl, ssh, rm and package installs are denied.
- **Limits:** default $1.50, hard maximum $5 per task, 20-minute timeout, one task at a time, $5 a day for coding in its own budget line (never counts toward the $5 voice cap). Runaway spend past 125% of the cap is killed; timeout and cancel kill the whole process group.
- **Voice:** "have the coding agent <goal> in <project>" stages a preview, a separate yes starts it, Julius announces the result, "show the changes", "merge it" reads the diff summary, a separate yes merges (never deletes, never overwrites a file edited since, backups kept), "undo the merge", "discard the coding task". The Sonnet planner stages tasks through `code_task` the same way.
- **Free tests:** 56 (project vetting, copy, command and environment, a real seatbelt escape attempt, runner, voice flow, planner tool). **Paid eval** (`evals/code_eval.py`, in `run_all.py`): bugfix, push refused, other-folder refused, injection README, cap hit. 5/5 on the final run for about $0.25. Opus mostly declined the bad requests by itself (the appended rules), so the hard guarantees rest on the seatbelt tests, not on the model's behaviour.
- **Known limits:** the stop hotkey stops speech, not a running coding task (say "cancel the coding task"); a task cannot see other projects, so cross-project work is out of scope.

### Network lockdown for the coding agent (2026-11-09)
`julius_egress_proxy.py`: a CONNECT-only proxy on 127.0.0.1 (OS-chosen port) that exists only while a coding task runs and allows exactly `api.anthropic.com:443`; everything else gets 403 (plain HTTP verbs 405), names resolving to non-public addresses are refused, TLS stays end to end. The seatbelt profile now denies all network except a connection to that one local port (no direct 443, no DNS), and the child gets `HTTPS_PROXY`/`HTTP_PROXY`. Refusals are logged per task in `egress.log` (host and port only). Tests (`test_julius_egress_proxy.py`, 11) include a real sandboxed process: direct connections to a local listener and to 1.1.1.1 fail, only the proxy port opens, github.com and pypi.org are refused through it. Live: the code eval cases bugfix, push_refused, injection and other_folder pass through the proxy.

### Research agent (2026-11-09)
`julius_research.py`: the Sonnet planner calls `research_task(question)`; a Haiku sub-agent (so the voice model stays light) searches and reads pages and returns a spoken summary plus a structured result (cited findings, sources, images with the page they came from) saved to `~/.julius/research/*.json` for the side panel. Read-only: the only network verb is GET, no forms, no cookies, login pages skipped; http/https ports 80/443 only, every resolved address must be public, connection pinned to the checked address, redirects re-checked (max 3), 500 KB limit, text pages only. Page text is untrusted data (tagged, with a standing rule). A finding survives only if its URL is a page the agent actually opened; a quote counts as verified only if it appears in that page. Caps in `[research]`: 8 model calls, 5 pages, $0.10 per task (the call that would pass the cap is not made); on a limit the result is partial and says so. 25 offline tests (`test_julius_research.py`) and a paid eval on a fixture web (`evals/research_eval.py`, in `run_all.py`): 15/15 runs accurate, sourced and safe, about $0.0004 and 3 model calls per task, 3.7 s. The eval uses a fixture web so answers are known; it does not measure real-web search quality.

### Phase C status (2026-11-06)
Built, unit-tested (588 tests) and evaluated: file agent 43/43 (free), planner eval 25/25 clean runs (paid, $0.27), coding agent eval 5/5 (paid, $0.25). **Complete (2026-11-06).** Live voice tests by the user: sandbox trash, yes and undo worked; the coding agent fixed demo_calc, showed, merged (own yes) and undid the merge. Live failures found and fixed on the way: glued wake word and ASR splits, status question dropping a pending plan, name-only trash blocked by "in the trash", bare 'undo' swallowed by the memory handler, spoken 'yes' dropped as self-echo after 'say yes to confirm', task folder under ~/.julius making every Edit fail (eval now runs at the real location), status question not recognised, garbled speech cancelling a pending start, .pyc caches listed in merge previews. Final numbers: 616 unit tests, routing 97.0% (131/135), file agent 100%, coding agent eval 5/5 ($0.21), planner eval 25/25. Known: a long garbled transcript of Julius's own voice can still slip past the echo filter and reach Haiku (dropped turns are now logged with a reason).

**Known limitations**
- The stop hotkey does not cancel a coding task (by decision); say "cancel the coding task".
- Opus 5.5 pricing is not in `julius_costs.RATES`; coding spend uses the dollar figure the CLI reports.
- "what did you change today" now says so when nothing changed today (fixed 2026-11-06); before it listed the last change whatever its date.


### Voice recording findings, 2026-11-07 (first six conversations) and fixes
- Saying "stop" over Julius did nothing: the microphone is shut while he speaks and voice barge-in was off. Fixed: talk-over detector (julius_talkover.py) stops speech when the user speaks over it; a bare "stop" gets no reply. Still level-based, not echo cancellation (F3a), so loud playback with a quiet voice may be missed; the hotkey remains.
- "Nah" / "Sure, go for it" were not recognised as answers to a pending plan, and a glued "Trashnotes.txt" skipped the file agent, so Haiku improvised a nonexistent tool and the failed call left the history malformed (every later turn got a 400). Fixed: julius_confirm.py shared by file/code/calendar, lowercase glue repair, unknown tools answered instead of spoken, history repaired each turn.
- A 2 s pause split "what time is it in London" into fragments. Fixed: dangling transcripts wait up to 4 s for the rest.
- "Thanks, go to sleep" did not sleep. Fixed: courtesy words tolerated.
- Long sentences stuttered (about 30 underflows in one story). Fixed by speaking clauses of up to about 110 characters; not yet re-measured on a recording.

### F2 baseline and F3a findings, 2026-11-07
Baseline from the 10 recorded conversations (38 turns; takes were recorded across code versions, so some failures were fixed after they were recorded):
- Live scoring (`python evals/voice_eval.py`): handled correctly 32/38 (84%); mean WER 0.106, 28/37 transcripts exact; 2 turns split by pauses; speech end to first audio p50 3.3 s, p95 5.8 s.
  The 6 failures: s04 x2 (natural yes/no, glued filename: fixed), s05 x2 (pause split the sentence: fixed by the 4 s hold), s07 status after finish (fixed), s06 interrupt (stop works only when loud; needs AEC).
- Front-end replay (`python evals/voice_eval.py replay --mode gate|none|aec`): gate WER 0.285 (25 utterances of Julius hearing himself), no gate WER 1.62 (93 self-heard), AEC WER 0.83 (59 self-heard).
- Echo cancellation (WebRTC AEC3 via livekit 1.1.21, vetted: Apache-2.0, official LiveKit wheel; julius_aec.py) removes only 3-6 dB of echo on these recordings. Reasons found:
  1. out.wav is stamped when audio is WRITTEN to the device; the real playback lags that by 0.4 s mid-reply and about 1.8 s when the output stream has just reopened, so the reference is misaligned.
  2. The laptop speaker-to-microphone coupling is weak (echo about 0.005 against 0.1 played), only 10x above the noise floor, and the user's quiet voice is 0.005-0.014: the same level as the echo.
  Next: callback-timed playback so the reference is sample-accurate, then a controlled self-test (Julius speaks, microphone records) before asking the user to re-record.

### F3a self-test result (2026-11-07)
`diagnostics/aec_selftest.py`: Julius's recorded voice played through ONE duplex audio stream (microphone and speaker on the same device clock), the reference being the exact block handed to the speaker.
Result: coupling 0.068 (echo 0.0075 against 0.11 played), WebRTC AEC3 echo return loss enhancement 28.7 dB, residual 0.0003 (below the 0.0008 noise floor and the 0.0045 speech threshold), stable over 50 s.
So echo cancellation works once the reference is sample-aligned; the recording misalignment, not the canceller, was the problem. Build: duplex stream at 16 kHz (TTS 24 kHz resampled), SpeechPlayer feeding the speaker side of the callback, cleaned microphone into the existing queue, the gate dropped, talk-over = speech detector on the cleaned signal. Behind `audio.duplex` until verified live.

## 2026-11-07 — F3(a) duplex audio path (built; became the default the same day, see "Voice barge-in: DONE" below)

What: `julius_duplex.py` runs one `sd.Stream` at 48 kHz (his voice at full quality; 24 kHz TTS is upsampled 2x). The microphone and the exact `outdata` of the same
callback are decimated to 16 kHz together (same filter, so they stay aligned) and go through WebRTC AEC3 (`julius_aec.EchoCanceller(lag=0)`). `SpeechPlayer` runs on
it unchanged through a shim (`DuplexAudio.shim()`), so prefill/underflow logging/abort still work; `abort()` empties the buffer at once.
With `audio.duplex = true` (or `JULIUS_DUPLEX=1` for one run): mic gate bypassed, cleaned audio goes into the normal `q`, `CleanTalkOver` (energy on the cleaned signal,
not echo-correlated, 0.3 s sustained, not in the first 0.4 s of a reply) stops him and keeps the last 4 s of audio as the next turn.
Safety: `AecMonitor` — residual above 2.5x room noise (and above 0.002) that moves with the playback loudness for `fallback_seconds` (3.0) of playback puts the mic gate back
(`duplex_fallback` event in the session log and the recording). The user's own voice does not follow Julius's envelope, so it does not count as leaking echo.
Recorder: duplex takes save the raw mic and the aligned reference, plus a `mic_start` event, so replay needs no clock guessing (`replay_frontend` handles both kinds).

Tests (test_julius_duplex.py, 20): reference == decimated callback output sample for sample; >15 dB echo reduction on synthetic speech through a delayed room; fallback fires in
2-7 s with a canceller that does nothing and does NOT fire with a working one or when the user talks over it; barge-in fires within 1 s of speech (also at 0.005 rms) and never on his
own voice over 14 s; abort unblocks a waiting write; SpeechPlayer works on the shim; switch is off in DEFAULTS and julius.toml. 680 tests pass. A 3 s run on the real MacBook
devices opened the 48 kHz duplex stream with 100 callbacks/s, 0 output underflows (1 input overflow at start), room noise 0.0003.

Replay on the existing ten conversations (recorded with the write-time reference, so this is NOT a test of duplex): gate — 38 turns, mean WER 0.303, exact 20/37, split/lost 12,
self-heard 24; aec (lag-estimated reference) — WER 0.537, exact 19/37, split/lost 15, self-heard 60. As expected the misaligned reference makes cancellation worse than the gate;
it is the reason for this build. The duplex comparison needs new recordings: `python evals/voice_record.py record --duplex d1 d2 d3`, then `python evals/duplex_report.py`
(self-echo turns, false interruptions, barge-in stop/understood/answered, fallbacks, echo reduction dB). Pass line: 0 self-echo turns, 0 false interruptions, every barge-in stops
and is answered, no fallback. Until then the switch stays off.

### 2026-11-07 first live duplex run (d1, d2, d3)
Result: d1 (two ~60-80 s stories, silent) — 0 self-echo, 0 false interruptions, echo reduction 31.7 dB, no fallback. d3 normal exchange — fine. d2 barge-in: he DID stop each time, but late
and with damaged words. Cause (from mic.wav/out.wav of the take): while Julius is loud the canceller's suppressor zeroes the cleaned signal for ~0.5 s at a time (0.0000) and only short
bursts of the user's voice pass, so a "sustained level for 0.3 s" detector fired ~1.7 s after he started talking, i.e. at the end of the sentence; the lost/garbled words ("Third, Springfield")
are the same suppression (double talk). Also a second talk-over fired while he was only thinking (play_age was still high), cutting the next turn.
Fix: CleanTalkOver now counts bursts (3 x 50 ms above threshold within 1 s), only while playback is audible right now, not in the first 0.4 s of a reply, not in the first 3 s of
playback since start (canceller converging; this removed one false trigger at 201.06 on replay). Replay of the d2 take: triggers 1.1 s and 0.7 s earlier than before, 0 false triggers in d1.
Still to confirm live: that the words are intact now that he stops ~0.2 s after the user starts. If not, next options: transcribe the raw mic for the overlap stretch, or lower the suppressor via a different AEC.

### 2026-11-07 second d2 run: the real cause of "he stopped answering" was in the speech worker, not duplex
First interruption worked end to end (stopped within ~0.3 s, "What's the weather in Springfield?" heard exactly and answered). Then every later reply was silent: "speech unavailable: No such file ... 3.npy".
Cause (old bug, any stop while a reply is still being generated): when the consumer stops reading mid-reply, `julius_tts.synthesize()` -> `suspend()`, which returns immediately under
JULIUS_KEEP_VOICE=1 (cloud profile), so the worker kept generating and its chunk lines stayed in the pipe; the next reply read those stale lines as its own and its cleanup had deleted the files.
Fix: `_abandon_stream()` signals the worker (SIGUSR1), which stops, sends `done(cancelled)`, and stays loaded; the host discards everything up to that ack (0.32 s with the real worker); anything unexpected
terminates the worker so the next reply starts fresh. test_julius_tts_cancel.py (fake worker) + a direct check against the real worker. The always-on service needs a restart to get this.

### 2026-11-07 fourth d2 run: three more defects
1) Barge-in transcript lost the start of the sentence ("In Springfield." for "Stop. What's the weather in Springfield?"): the suppressor mangles the cleaned signal for the first words while Julius is loud.
   Fix: the duplex stream keeps the last ~5 s of raw mic blocks; on barge-in the queued blocks from (onset + 0.7 s) back are replaced by their raw versions and the next 12 blocks go out raw
   (offline on take4: cleaned-only "What's the..." -> with raw backfill "What's the weather in Springfield?").
2) He read his own instructions aloud ("The system says replies default short, but he explicitly asked for a long one..."). META_LEAK widened; BREVITY_PROMPT now allows long stories on request and forbids mentioning rules.
3) Dates were read "November oh-eight": tool output used %d (zero padded) and normalize_for_speech had no month-day rule. Now %-d at the source and "November eighth" in speech (ISO and US dates too).
Also the recording script waited only 12 s after the stop reply, so it moved on while he was still answering the interruption; it now waits for the second reply (up to 45 s).

## 2026-11-07 — Voice barge-in: DONE (duplex audio is now the default)
Decision (the user, 11-07): `audio.duplex = true` default; recognizer input comes from the echo-cancelled buffer only (`audio.raw_backfill = false`; the raw backfill recovered words but let Julius's own
voice into transcripts, e.g. "8, 20, what's the weather in San Jose?"). Interrupted turns are stored in history as "[interrupted by the user]" and the prompt says an interruption is a change of
topic, not a complaint (no apology, no refusal of long answers afterwards, long answers still given in full when asked).
Live numbers (all on the real MacBook mic/speakers, recordings in voice_data/, local only):
- d1 silent stories (2 x 60-80 s): self-echo 0, false interruptions 0, echo reduction 31.7 dB, no fallback.
- d2 / d2q / d2f (normal, quiet "like people were sleeping", and fast "stop, <question>" in one breath): every interruption stopped him (~0.3 s after speech began, speakers silent at once),
  was understood and answered (Springfield weather, Tokyo time, San Jose weather); no false stops; no fallback. Replay of the fast-stop take through the cleaned-only path: "Stop." transcribed in 3/3.
- Defects found and fixed on the way: 12-fold re-trigger cancelling the next turn; speech worker left stale chunks after an abandoned reply (all later replies silent); instructions read aloud;
  "November oh-eight"; refusing to list fifty states ("long ramble"); suppressor mangling the user's first words (burst-based detector instead of sustained level).
Known limits: the first word of a fast interruption is occasionally garbled; Haiku may still ask "which kind?" for story requests after several interruptions in one conversation (history, not audio).
Safety net: AecMonitor falls back to the mic gate by itself after 3 s of leaking echo (event `duplex_fallback`); `JULIUS_DUPLEX=0` disables duplex for one run.

**F3 finished (2026-11-08):** (f) no made-up live data: `julius_livedata.py` drops any sentence that states a figure about a changing thing (score, price, time, count) when no tool result backs it, says "I can't look that up" instead; prompt rule LIVE FACTS; eval `evals/livedata_eval.py` (12 cases, paid, a few cents; not yet run). (d) `julius_ack.py`: "On it." once if a tool call outlasts 1.5 s with nothing said yet (streamed cloud speech only). (c) `julius_lowconf.py`: shaky recognition (worst segment log-probability below -0.9 or no-speech above 0.35) plus a risky request (trash, move, delete, forget, merge, send, ...) is read back as "Did you say ...?" and runs only on a yes. (g) `julius_undo.py`: bare "undo" undoes the most recent action of any agent (files, coding merge, memory) and names it. Silero VAD (e) skipped unless the soak shows endpointing problems.

**Live-data follow-up (2026-11-09):** `evals/livedata_eval.py` run: 12/12, then two gaps found and fixed. (1) `julius_tickers.py`: company and crypto names resolve to tickers before the price tool (Apple to AAPL, Bitcoin to BTC-USD; table first, then a Yahoo lookup; several different companies make the tool answer AMBIGUOUS so he asks which one). (2) `julius_tools.live_tool_required`: a question about a score, price, weather, schedule or news forces a tool call on the first model call of the turn (`tool_choice: any`), even if the topic came up earlier; "price of gas" and non-requests ("thanks for the score") are excluded. Eval now 14/14: tool cases need a tool call in that turn, plus Microsoft and an ambiguous Delta (`ask`). 800 tests.

**F4 soak started 2026-11-08 00:13:48 (24 h, ends 2026-11-09 00:13).** Service restarted on the final F3 code; `python -m julius.service.service smoke [--restart]` (ready, both hotkeys, duplex audio, no crash loop, no traceback) passed on the fresh start and `julius_service.py doctor` shows both hotkeys register. Read it with `python -m julius.service.soak report --since 2026-11-08T00:13` and `python -m julius.service.logscan --since 2026-11-08T00:13` (launchd stderr is now marked at each restart, so `--since` no longer counts the old crash-loop tracebacks).

**UI event feed (built 2026-11-08, during the soak; no UI yet):** `julius_events.py` is a read-only WebSocket on 127.0.0.1:8765 (`[events]` in julius.toml; stdlib only, no new package). Messages: `hello` on connect (state, level, last 20 transcript lines), `state` (idle / listening / thinking / speaking / asleep) on every change, `level` (Julius's voice as it leaves the speakers, 0-1, about 30 Hz while he speaks, then one 0), `transcript` (`you` = what the user said, `julius` = each sentence as he speaks it). Clients cannot send anything that has an effect; foreign Host or Origin headers are refused; 8 clients max; slow clients are dropped. Test client: `python -m julius.ui_bridge.events_client [port] [--levels]`. 12 tests. **Not live in the service yet:** the service was deliberately not restarted so the soak keeps its 24 h of uninterrupted running; the feed starts at the next restart (after the soak ends, 2026-11-09 00:13).


- 5c-music item 4 (session busywork): audio workspace, preview/confirm/undo rename and organize, batch convert as a file-agent plan: built; the drive entries wait on macOS permission for Julius's own Python (~/.julius/env-service).

- **5c-music: done (2026-11-14).** BPM and key detection, theory help (genre and era styles, extended and altered chords, piano card), the allowlist-only sample index (folders, single files, name patterns, the CHZN drive by volume UUID, offline marking), the audio workspace with batch convert through the file agent, and the spoken rescan command. Song identification is deferred to ShazamKit until there is an Apple Developer account (AcoustID evaluated and skipped; the acceptance test is kept above). Real-music tempo accuracy was measured on 200 of your own files (see the item 1 paragraph). The CHZN drive is indexed (2,212 files, 53.8 GB, 2026-11-14) once Full Disk Access was granted to `~/.julius/env-service/bin/python3.12` and the service launched that interpreter directly.

- **5c-art portrait pass and camera, 2026-11-14.** The live check of pixel art on a selfie failed (a whole photo at 32 px, naive 4-colour mapping), so: face detection (Apple Vision, local) and a head-and-shoulders crop, 64 px portrait default, bilateral smoothing, auto-levels and luminance mapping for small palettes, optional Bayer dither, strong-edge outline, a "retro" sprite mode and a comparison grid; then "take a photo / selfie" with a live countdown, held in memory. Step C follows.
- **5c-art, approved 2026-11-14 (plan), steps A and B built the same day.** Approved additions: pixel-art export as a nearest-neighbour upscale (2x/4x/8x) with a transparent-background PNG option, saved by "save this card" into `~/Julius Saved/`; logo to SVG with **vtracer** (MIT, verify from its official repo; potrace is not used at all); verify the OpenCV and scikit-image licences before using either; step F (grid editor) also covers sprite-sheet slicing/assembly and an animation preview for game work, still last and optional; images in only as dropped images or files from approved folders, never from purchased asset packs. **A (shared core) and B (photo to pixel art) are built** (`julius/art/`, `pixel_art` tool, `panel.art` card; see `docs/ART.md`): Pillow and numpy only, so no new dependency and no licence question yet. Next: C (style filters), D (tracing guide and digitizer), E (logo to SVG), F (grid editor, sprite sheets, animation preview).

- **Sprite mode (head-only, built 2026-11-15)** is the first piece of the Sprite studio: Vision landmarks and person mask to a deterministic head sprite (see ART.md). The camera moved into a native floating window in the orb the same day.
- **Editable sprites (built 2026-11-16)**: layers and parameters, voice and card edits, the sprite editor window (step F's pixel editor, pulled forward for sprites: per-layer edits, undo/redo, palette locked), `.julius-sprite` files. See ART.md.
- **Sprite studio (planned, 2026-11-14; after steps C to F; the head is done, the body is not).** Plan only.
  - **Rotoscope mode:** record a short move with the camera (walk, run, jump, punch, idle). Apple Vision person segmentation and body pose run locally. Every frame goes through the pixel pipeline at a locked scale, palette and outline so the character stays consistent; frame-to-frame colour locking stops flicker; onion-skin cleanup in the grid editor (step F); export a sprite sheet plus a frame-timing JSON.
  - **Cutout rig option:** the body is drawn as parts and animated by the coding agent (offsets and rotations, with pixel-safe rotation such as RotSprite).
  - **Optional open base bodies** (for example LPC) only with the licence and attribution recorded next to the asset.
  - **Generative stays out:** nothing is drawn from nothing; every pixel comes from the user's own camera, files or drawing.
- **Routing robustness (2026-11-14).** Diagnosis: r101 and r129 sat on a coin flip (conflicting planner rules; "what you know about X" reading like `list_memories`), so any change to the tool list or prompt flipped them, whatever the change was. Fixed with TIE-BREAKS in `PLANNING_PROMPT` (briefings escalate, forget-by-topic calls `forget_fact` directly, a bare "this picture/this track" means the dropped file) and tighter `forget_fact`/`list_memories` descriptions. `evals/routing_shake.py` perturbs the tool list and prompt and flags fragile cases; it is part of the checklist for every new tool (README). `image_style` (style `pixel_art` for now; step C adds styles) is a real brain tool that only restyles a picture the user provides; the plain pixel-art phrasings keep their fast pattern path. Held back: a two-step route (pick a tool family, then show only its tools).
