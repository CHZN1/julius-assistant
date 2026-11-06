# Julius roadmap

Updated October 29, 2026. Current priority order; older milestone documents (JULIUS_TEST_RESULTS.md, JULIUS_*.md) keep the historical measurements.

## Completed

- Diagnostics; git + private GitHub; moved out of iCloud to `~/dev/julius`.
- Cloud brain: Haiku 5.5 front end, Sonnet 5.5 planner, prompt caching, streaming with tools, daily spend cap, per-turn cost log, offline llama3.2:3b fallback, spoken auth-error message.
- Voice: MLX seed-1301 voice kept loaded, underflow-safe audio player, speech text normalization.
- Speech recognition: small.en, 0.6 s silence wait, transcript repair.
- Router shortcuts with filler stripping and follow-up carry-forward; one round trip for simple tools.
- Sonnet-controlled file agent with confirmation.
- Cloud profile is the default everywhere (`python julius.py` and `run_julius.sh` use the same defaults; `--help` prints usage).
- 399 tests passing (394 plus the default-profile and eval-tooling tests).

## Phase A: Evaluation harness (now)

Everything lives in `evals/`; results are dated CSVs in `evals/results/` and each run reports regressions against the previous run of the same suite.

- [x] Recorded-audio regression suite (`evals/audio_regression.py`): replays the 20 recorded phrases plus the standard typed test prompts through ASR, router, brain and TTS (headless, no speakers); records transcription accuracy (WER, missed names/numbers), routing path and tool calls, first-audio latency and cost per turn.
- [x] Routing eval (`evals/routing_eval.py`, 105 cases in `evals/routing_cases.jsonl`): typed prompts labeled with the expected path (shortcut / direct answer / which tool / escalate to planner), scored automatically. A free deterministic mode (`--no-brain`) checks the shortcuts alone.
- [x] File agent eval in a sandbox folder.
- [x] A one-command eval run (`evals/run_all.py`) that runs every suite, writes the dated results CSVs and flags regressions against the last run.

First baseline (2026-10-29): routing eval 89.5% (94/105); recorded audio mean WER 8.0%, 69% of turns fully correct, median first audio 1.6 s, $0.0004 per turn. Details and the list of misses are in `evals/baselines/README.md`.

**Phase A complete (2026-10-30).** Routing 97.5-98.3% (119 cases), file agent eval 100%, scores tool handles weekdays/dates/last week, tool-turn first audio 1.66 s. ASR vocabulary hint stays on (held-out difference was one apostrophe, no hallucinated hint words; details in `evals/baselines/README.md`).

## Phase B: Daily driver for me

- [x] **Dedicated wake-word engine** (done 2026-11-05): `[wake] engine = "cascade"` in `julius.toml`. A noise-gated openWakeWord "Julius" detector (0.03% of a core idle) fires at a low threshold, then one Whisper run on the whole utterance checks the wake word (hint names Julius beside Julia/Julian/Julie/Jules) and gives the request; `engine = "whisper"` is the fallback. Optional personal speaker verifier in `~/.julius/` (currently off, see below). Every fire is logged to `julius_memory/wake_log.jsonl` with near-miss clips for retraining. Details: `wake/`, `julius_wake.py`, `evals/wake_eval.py`.
  - Measured on 60 fresh recordings (wake3 + wake4), 3.4 min of ordinary talk and 18 held-out Julius-voice lines: miss 3.3% (2/60), 0 false wakes per hour, Julius's own voice woke it 6 of 18 in isolation (live, `MicrophoneGate` also drops audio while he speaks). Previous model: 8.3% misses. The verifier cut Julius-voice wakes to 3 of 18 but added a miss (5.0%), so it ships off.
  - Open: retrain the detector and verifier on the real-usage log (`wake_log.jsonl` near-miss clips); make Julius's own voice a harder negative; longer ordinary-talk sets to tighten the false-wake estimate (3.4 min is a small sample).
- [x] **Barge-in: stop hotkey shipped** (2026-11-05): `[barge_in] hotkey` (default Ctrl+Option+S) and typed `/stop` silence Julius at once (`SpeechPlayer.abort()` plus cancellation). macOS needs your terminal allowed under Privacy & Security > Accessibility / Input Monitoring. Live fixes found on the way: the mic gate now covers the 0.4 s output latency, Julius's own words heard right after he speaks are ignored (`julius_selfecho.py`), the wake stream resets on capture gaps.
- [ ] **Voice barge-in: parked, off** (`[barge_in] voice = false`). Evaluated in isolation (`evals/bargein_eval.py`, speakers, one persistent AEC across turns, 3 long answers, 18 Julius-voice lines, 36 real double-talk takes; onsets are an energy annotation, 3 takes had no speech):
  - Energy only or a level check cannot separate your voice from his echo (your speech is about the level of his echo peaks). Self-transcript rejection removed most self-triggers (15 stops to 4).
  - WebRTC AEC (31.5/12.6/29.3 dB on the long answers, 1.4% real time) is best: it ducked 7 of 8 stop takes within 300 ms (median 20-40 ms), with 6 of 7 questions; but 1 of 8 Julia/Julian sentences still confirmed as a stop (earlier scoring 2 of 9), the stage-1 "Julius" detector confirms only ~1.1 s after the duck and also fires on Julia sentences, and the first 3 s of each playback are unreliable. Scoring moved between runs because the onset annotation is unstable.
  - Needs before it ships: a small "stop" keyword model (bare "Stop." is invisible to the Julius detector) plus a Julia-aware confirm step, then re-run the eval on more takes with human-labelled onsets. Revisit with the Phase F orb UI. Headphone detection (skip AEC, allow direct barge-in) is a cheap follow-up.
- [ ] **Always-on launchd service** (built 2026-11-05, not yet installed; `python julius_service.py on|off|status|restart|wake|logs`): LaunchAgent `com.julius.assistant` (RunAtLoad, KeepAlive on crash only, 15 s throttle, no keys in the plist); structured JSON log in `~/Library/Logs/Julius/julius.jsonl` (5 MB x 5, speech redacted unless `[service] log_transcripts`); quiet mode: "Julius, go to sleep" turns the detector fully off (persisted across restarts) until the wake hotkey (`[service] wake_hotkey`, Ctrl+Option+J), `julius_service.py wake` or typed `wake`. Still to verify after install: microphone and Accessibility permission for the launchd-started Python, idle CPU/memory over a day, restart-after-crash.
- [ ] Long-term memory across sessions, stored locally, with "forget that".
- Also: reminders/calendar polish.

## Phase C: Agents

Julius is the orchestrator; agents are sub-agents dispatched and supervised by the Sonnet planner.

- File agent hardening: preview of changes, undo log/recoverable trash, per-folder permissions, its own eval set.
- Coding agent: Opus 5.5 as a sub-agent (compare the Agent SDK vs `claude -p`), with a per-task cost cap.

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
