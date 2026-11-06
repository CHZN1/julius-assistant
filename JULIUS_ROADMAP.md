# Julius roadmap

Updated November 5, 2026. Current priority order; older milestone documents (JULIUS_TEST_RESULTS.md, JULIUS_*.md) keep the historical measurements.

## Completed

- Diagnostics; git + private GitHub; moved out of iCloud to `~/dev/julius`.
- Cloud brain: Haiku 5.5 front end, Sonnet 5.5 planner, prompt caching, streaming with tools, daily spend cap, per-turn cost log, offline llama3.2:3b fallback, spoken auth-error message.
- Voice: MLX seed-1301 voice kept loaded, underflow-safe audio player, speech text normalization.
- Speech recognition: small.en, 0.6 s silence wait, transcript repair.
- Router shortcuts with filler stripping and follow-up carry-forward; one round trip for simple tools.
- Sonnet-controlled file agent with confirmation.
- Cloud profile is the default everywhere (`python julius.py` and `run_julius.sh` use the same defaults; `--help` prints usage).
- 496 tests passing.

## Phase A: Evaluation harness (now)

Everything lives in `evals/`; results are dated CSVs in `evals/results/` and each run reports regressions against the previous run of the same suite.

- [x] Recorded-audio regression suite (`evals/audio_regression.py`): replays the 20 recorded phrases plus the standard typed test prompts through ASR, router, brain and TTS (headless, no speakers); records transcription accuracy (WER, missed names/numbers), routing path and tool calls, first-audio latency and cost per turn.
- [x] Routing eval (`evals/routing_eval.py`, 105 cases in `evals/routing_cases.jsonl`): typed prompts labeled with the expected path (shortcut / direct answer / which tool / escalate to planner), scored automatically. A free deterministic mode (`--no-brain`) checks the shortcuts alone.
- [x] File agent eval in a sandbox folder.
- [x] A one-command eval run (`evals/run_all.py`) that runs every suite, writes the dated results CSVs and flags regressions against the last run.

First baseline (2026-10-29): routing eval 89.5% (94/105); recorded audio mean WER 8.0%, 69% of turns fully correct, median first audio 1.6 s, $0.0004 per turn. Details and the list of misses are in `evals/baselines/README.md`.

**Phase A complete (2026-10-30).** Routing 97.5-98.3% (119 cases), file agent eval 100%, scores tool handles weekdays/dates/last week, tool-turn first audio 1.66 s. ASR vocabulary hint stays on (held-out difference was one apostrophe, no hallucinated hint words; details in `evals/baselines/README.md`).

## Phase B: Daily driver for me: complete (2026-11-05)

Julius runs all day as a login service, wakes on his name, can be silenced or put to sleep without any macOS permission, and remembers what you tell him. 496 unit tests pass.

- [x] **Wake word, cascade v2** (`[wake] engine = "cascade"`): a noise-gated openWakeWord "Julius" detector (0.03% of a core idle) fires at a low threshold, then one resident Whisper small.en run over the whole utterance checks the wake word (hint names Julius beside Julia/Julian/Julie/Jules) and gives the request. Final numbers (`evals/wake_eval.py suite`, 60 fresh recordings, 3.4 min of ordinary talk, 18 held-out Julius-voice lines): **misses 0/60 (0.0%), false wakes 0 per hour** (3.4 min is a small sample), Julius's own voice woke it 6 of 18 in isolation (live, the mic gate and self-echo check stop this in practice). Previous model: 8.3% misses. Every fire is logged to `julius_memory/wake_log.jsonl` with near-miss clips for retraining.
- [x] **Hotkeys, no permissions**: Ctrl+Option+S stops generation and speech at once (`SpeechPlayer.abort()` plus cancellation, also typed `/stop`); Ctrl+Option+J wakes from quiet mode. Both use Carbon `RegisterEventHotKey` through a small C helper (`hotkeyd/hotkeyd.c`, built on first use into `~/.julius/bin`, exits with Julius), so neither Accessibility nor Input Monitoring is needed (pynput could not be trusted under launchd). `julius_service.py doctor` checks both register. Verified live on the running service: stop mid-answer, "go to sleep" then the wake key.
- [x] **Always-on launchd service with quiet mode** (`python julius_service.py on|off|status|restart|wake|doctor|logs`): LaunchAgent `com.julius.assistant` (starts at login, restarts on crash only, 15 s throttle, no keys in the plist); structured JSON log `~/Library/Logs/Julius/julius.jsonl` (5 MB x 5, speech redacted unless `[service] log_transcripts`); `logs` shows recent lines and follows. "Julius, go to sleep" turns the detector fully off (persisted across restarts) until Ctrl+Option+J, `julius_service.py wake` or SIGUSR1. Still unmeasured: idle CPU/memory over a full day, restart-after-crash.
- [x] **Long-term memory** (`julius_memstore.py`, `julius_memcmd.py`, `julius_memory/memory.sqlite3`): "remember that…", "forget that / forget <topic> / forget everything", "what do you remember (about X)", "what did you just remember", "undo that", `/memories`; corrections supersede facts; automatic capture of durable facts (regex prefilter, Haiku extraction, marked auto, "Noted." once per turn; facts about Julius or secrets are never stored); forgetting hard-deletes and scrubs session logs; notes ride after the cached prompt, 400-token cap with newer facts winning, framed as data. Natural forget phrasings are caught before Haiku, a "forget that" with no target asks which one and takes the answer, and Haiku has `remember_fact` / `forget_fact` / `list_memories` tools as a backup (they only act on what the user said that turn). Eval `evals/memory_eval.py` (suite "memory" in run_all): **7/7 sections pass**: 70 command phrasings incl. negatives 100%, forgetting 0 residue, recall 100% (40 questions, 30 facts), auto-capture precision 96.2% / recall 100% (60 labelled sentences), 8 headless restart/forget/injection scripts all pass. Empty schema, fictional sample and README for open source in `julius_memory.example/`.
- [ ] **Voice barge-in: parked, off** (`[barge_in] voice = false`; the hotkey covers stopping). Evaluated in isolation (`evals/bargein_eval.py`, speakers, persistent AEC, 36 real double-talk takes): WebRTC AEC ducked 7 of 8 stop takes within 300 ms but 1 of 8 Julia/Julian sentences still confirmed as a stop, and the first 3 s of each playback are unreliable; energy cannot separate your voice from his echo. **Needs before it ships:** a small "stop" keyword model (bare "Stop." is invisible to the Julius detector), a Julia-aware confirm step, then a re-run on more takes with human-labelled onsets (revisit with the Phase F orb UI). Headphone detection is a separate low-effort item.

Known open items:
- Wake verifier: built but **off** (it cut Julius-voice wakes from 6 to 3 of 18 but added a miss); retrain on the real-usage log.
- Julius's own voice waking him in isolation (6 of 18); live it is blocked by the mic gate and self-echo check, but a harder negative in the detector training set is the real fix.
- One auto-capture false positive in the eval ("I have to pick up the kids at four" is stored as a fact); tighten the extraction prompt on plans and errands.
- Unmeasured: service idle CPU/memory over a day, restart-after-crash, live Whisper-wake timing after sleep.
- Reminders/calendar polish.

## Phase C: Agents (next)

Julius is the orchestrator; agents are sub-agents dispatched and supervised by the Sonnet planner. Build order:

1. **File agent hardening** (do first: it touches the user's files)
   1. Change preview: every move/rename/delete shows exactly what will happen before confirmation.
   2. Undo log and recoverable trash: nothing is permanently deleted; every change is journaled and reversible.
   3. Per-folder permissions: read/write/none per folder, defaults to Documents, Desktop and Downloads only.
   4. Its own eval set: sandbox scenarios for preview accuracy, undo round trips, permission denials and prompt-injection from file contents; added to `run_all.py`.
2. **Coding agent**: Opus 5.5 as a sub-agent of the Sonnet planner.
   1. Compare the Claude Agent SDK against `claude -p` (tool control, streaming, session resume, permission model, cost reporting); pick one.
   2. Per-task cost cap enforced by Julius, with a spoken warning and a hard stop.
   3. Eval suite: small repo tasks with tests as the oracle, cost and time per task, refusal/stop behavior, no writes outside the project folder.

## Phase D: Sports analytics agent (differentiator)

Julius supervises my NHL/NFL/MLB/WNBA pipelines in `~/dev` as sub-agents: run today's predictions, summarize results and parlays, report model performance, trigger retraining, all with confirmation before anything writes or bets.

## Phase E: Open-source release (portfolio)

- Audit the full git history for secrets and personal data (API keys, .env, julius_memory, planner state, my recorded voice phrases); report findings before any history rewrite.
- Bring-your-own Anthropic key via `.env.example`; clear setup README with requirements (Apple Silicon, memory), a demo GIF/video, architecture diagram, and cost-per-turn table.
- MIT license.
- Wake word: the shipped detector (`wake/models/julius.onnx`) and the default `engine = "cascade"` path must work for anyone without the personal speaker verifier. The verifier is a personal layer trained on one person's voice (`python wake/verifier.py train`) and stored outside the repo in `~/.julius/`; the repo must not contain recordings, the verifier file or `julius_memory/wake_log.jsonl`. The detector was trained with the author's wake recordings as part of its positives: before release, retrain it from synthetic voices only (or ship it clearly labelled) and document how to record a personal wake set. Confirm the openWakeWord models download step (`openwakeword.utils.download_models`) is in the setup README.

## Phase F: UI

Siri-style animated orb (listening, thinking, speaking states), transcript bubble, push-to-talk hotkey. Design details to come from me.

Note on `native/apple/bridge.js`: it is not UI. It is the JavaScript-for-Automation bridge that `julius_apple.py` pipes JSON into (via `osascript`) to read Calendar and Reminders inventories and to create, complete and cancel tokenised events and reminders. It stays an integration for Phase B reminders/calendar; Phase F needs a new front end.

## Later

Vision branch (beyond emotion detection), hosted version with subscriptions.
