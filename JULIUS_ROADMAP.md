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

## Phase B: Daily driver for me

Long-term memory, reminders/calendar, barge-in (interrupt Julius while he talks), a dedicated low-CPU wake-word engine instead of Whisper transcripts, an always-on launchd service with crash recovery, structured logging.

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

## Phase F: UI

Siri-style animated orb (listening, thinking, speaking states), transcript bubble, push-to-talk hotkey. Design details to come from me.

Note on `native/apple/bridge.js`: it is not UI. It is the JavaScript-for-Automation bridge that `julius_apple.py` pipes JSON into (via `osascript`) to read Calendar and Reminders inventories and to create, complete and cancel tokenised events and reminders. It stays an integration for Phase B reminders/calendar; Phase F needs a new front end.

## Later

Vision branch (beyond emotion detection), hosted version with subscriptions.
