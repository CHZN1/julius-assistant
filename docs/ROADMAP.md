# Julius roadmap

Updated November 9, 2026. Current priority order; older milestone documents (JULIUS_TEST_RESULTS.md, JULIUS_*.md) keep the historical measurements.

## Status at a glance (2026-11-09)

| Phase | State |
|---|---|
| A Evaluation harness | done |
| B Daily driver (wake word, hotkeys, service, memory, voice barge-in) | done |
| C Agents (file, coding, planner, research) | done; research agent and coding-agent network lockdown added 2026-11-09 |
| D Sports analytics | paused until the user's models are ready |
| E Open-source release | prep: audit, wake-model retrain, README, .env.example, MIT licence (see Phase E); nothing removed or rewritten yet |
| F Voice polish and reliability | done (F1 to F3 built and live; Silero VAD skipped unless endpointing problems show; F4 soak ran 2026-11-08 to 11-09) |
| H Senses (vision on demand, presence and gestures, ShazamKit, speaker profiles, whiteboard to text) | planned, nothing built |
| G UI (orb app) | modes, typing, drops, installed app and login item built and in live testing; side-panel work (charts, images, vision, pixel art) waits for the user's go; image generation deferred (no provider) |

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
- [x] **Always-on launchd service with quiet mode** (`python julius_service.py on|off|status|restart|wake|doctor|logs`): LaunchAgent `com.julius.assistant` (starts at login, restarts on crash only, 15 s throttle, no keys in the plist); structured JSON log `~/Library/Logs/Julius/julius.jsonl` (5 MB x 5, speech redacted unless `[service] log_transcripts`); `logs` shows recent lines and follows. "Julius, go to sleep" turns the detector fully off (persisted across restarts) until Ctrl+Option+J, `julius_service.py wake` or SIGUSR1. Still unmeasured: idle CPU/memory over a full day, restart-after-crash.
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
- Wake word: the shipped detector (`wake/models/julius.onnx`) and the default `engine = "cascade"` path must work for anyone without the personal speaker verifier. The verifier is a personal layer trained on one person's voice (`python wake/verifier.py train`) and stored outside the repo in `~/.julius/`; the repo must not contain recordings, the verifier file or `julius_memory/wake_log.jsonl`. The detector was trained with the author's wake recordings as part of its positives: before release, retrain it from synthetic voices only (or ship it clearly labelled) and document how to record a personal wake set. Confirm the openWakeWord models download step (`openwakeword.utils.download_models`) is in the setup README.

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

The UI lives in its own **private** repo, `a private UI repo` (`a private UI repo`, split out 2026-11-09 with its history). This repo keeps only the interface: the read-only event feed (`julius_events.py`, `docs/EVENT_FEED.md`, `julius_events_client.py`, a test that the document matches the code) and the authenticated write channel (`julius_uichannel.py`, `julius_dropstage.py`, `docs/UI_CHANNEL.md`: typed text and dropped files; dropped files are only staged, nothing is opened or moved without the file agent's own yes).

Built: the animated orb (states asleep/quiet/offline/listening/thinking/speaking, 8 finishes, shake and brush physics, power caps, settings drawer), window modes (Mini 100 px with corner snap, Floating 420 wide, Full), typing in the orb, drag and drop onto the orb, click-and-drag to move, restart sync, `Julius Orb.app` in `~/Applications` with menu-bar Quit and Cmd+Q, and a login item (`julius_orb.py on`). Details and tests are in that repo's README.

Next: side-panel media (5a charts and images, 5b vision, 5c pixel art), starting when the user has tried the modes, typing and drops; 5d image generation deferred (no provider yet). The research agent already saves the images and sources the panel will show.

Note on `native/apple/bridge.js`: it is not UI. It is the JavaScript-for-Automation bridge that `julius_apple.py` pipes JSON into (via `osascript`) to read Calendar and Reminders inventories and to create, complete and cancel tokenised events and reminders.

## Phase H: Senses (later; plan only, nothing built)

Julius gets eyes, presence and more ears, always on-device where it can be and always confirmed per use when anything goes to the cloud.
1. **Vision on demand.** "What am I holding?", "read this", "what's on my screen?": one camera snapshot or screenshot sent to Claude vision, confirmed per use (spoken or on screen), never continuous video to the cloud. Builds on the side-panel image path and the research agent's image results.
2. **Local, low-power presence and gestures** with Apple's Vision framework (on-device, no frames stored or sent): wake on sit-down, go quiet when you leave, thumbs-up = yes, raised hand = stop. Frame-rate and CPU budget measured like the wake word (idle cost must stay near zero); off by default; a visible indicator whenever the camera is in use.
3. **Song recognition** with ShazamKit ("what's playing?"), on request only.
4. **Speaker recognition** for per-person profiles and memory, reusing the speaker-verifier work (`wake/verifier.py`) and the `[profile]` section: who is talking decides name, memory namespace and permissions; unknown voices get the guest profile.
5. **Whiteboard and handwritten notes to code, tasks or text**: a snapshot (confirmed per use) turned into a checklist, reminders, a code file proposal (staged through the coding agent, never written directly) or plain text.
Open design points: camera and screen-recording permissions flow, the consent UI in the orb, retention (nothing stored by default), and how per-person memory is kept apart on disk.

## Later

Vision branch (beyond emotion detection), hosted version with subscriptions.


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
(`duplex_fallback` event in the session log and the recording). the user's own voice does not follow Julius's envelope, so it does not count as leaking echo.
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

**F4 soak started 2026-11-08 00:13:48 (24 h, ends 2026-11-09 00:13).** Service restarted on the final F3 code; `python julius_service.py smoke [--restart]` (ready, both hotkeys, duplex audio, no crash loop, no traceback) passed on the fresh start and `julius_service.py doctor` shows both hotkeys register. Read it with `python julius_soak.py report --since 2026-11-08T00:13` and `python julius_logscan.py --since 2026-11-08T00:13` (launchd stderr is now marked at each restart, so `--since` no longer counts the old crash-loop tracebacks).

**UI event feed (built 2026-11-08, during the soak; no UI yet):** `julius_events.py` is a read-only WebSocket on 127.0.0.1:8765 (`[events]` in julius.toml; stdlib only, no new package). Messages: `hello` on connect (state, level, last 20 transcript lines), `state` (idle / listening / thinking / speaking / asleep) on every change, `level` (Julius's voice as it leaves the speakers, 0-1, about 30 Hz while he speaks, then one 0), `transcript` (`you` = what the user said, `julius` = each sentence as he speaks it). Clients cannot send anything that has an effect; foreign Host or Origin headers are refused; 8 clients max; slow clients are dropped. Test client: `python julius_events_client.py [port] [--levels]`. 12 tests. **Not live in the service yet:** the service was deliberately not restarted so the soak keeps its 24 h of uninterrupted running; the feed starts at the next restart (after the soak ends, 2026-11-09 00:13).

