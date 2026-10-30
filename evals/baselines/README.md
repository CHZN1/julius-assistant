# Eval baselines (2026-10-29)

Run on the cloud profile (Haiku 5.5 front end, Sonnet 5.5 planner, MLX voice). Both suites are single runs; cloud-brain outcomes vary a little between runs
(two back-to-back routing runs scored 90.5% and 89.5%, differing on three cases), so treat one-case moves as noise and look at categories.

## Routing eval: `python evals/routing_eval.py` (105 cases, about $0.03, about 2 minutes)

| | |
|---|---|
| Correct (path + tool + args) | **89.5%** (94/105) |
| Path correct / tool correct | 90.5% / 89.5% |
| Brain-stage mean latency | 1.52 s per decision |

By category (ok%): direct 100 (23), follow-up carry-forward 100 (9), live-intent shortcuts 100 (19), utility 85.7 (7), tool selection 83.3 (24), files 76.9 (13), escalate 70 (10).
Full per-case results: `evals/results/routing_20261029_224230.csv` (committed).

Misses, grouped by cause:
- **File shortcuts intercept planner work.** "Organize my downloads folder by file type" and "Sort my photos into folders by year" are caught by the deterministic file router, which answers with a clarification question instead of escalating to the planner. (Recorded phrase 16 fails the same way.)
- **"Remind me to call my mom at 3:45 PM" has no handler.** The utility session only accepts durations or ISO times, and the brain has no write tool, so Haiku explains it cannot add reminders.
- **Multi-lookup requests answered with a single tool.** "Weather in Springfield and the time in Tokyo", "Compare today's weather in Springfield and Tokyo", the three-item NHL/NFL/Ethereum summary, and the weather/Bitcoin/NHL briefing sometimes go to one tool instead of the planner. The last one is also a shortcut bug: the weather shortcut takes "Springfield, the price of Bitcoin, and the latest NHL scores" as the location.
- **"What day of the week is March 3rd 2027?"** is caught by the `what day` shortcut and answers with today's date.
- **Arithmetic and history questions answered without a tool.** "15 plus 8 divided by 2 squared" is computed by the model; "NFL scores from Sunday" got a refusal or an unrelated tool (the scores tool only covers today and yesterday).

## Recorded-audio regression: `python evals/audio_regression.py` (20 recorded phrases + 9 typed prompts, about $0.01, about 5 minutes)

| | |
|---|---|
| ASR (small.en + transcript repair), mean WER | **8.0%** on the 20 phrases, mean 0.24 s per phrase |
| Phrases with a missed name/number/acronym | 3 of 20: #5 "AcmeCorp" (heard "price big"), #18 "Pat Example" (heard "patching my home"), #19 "FER 2013" (heard "FVR 2013") |
| Routing: path correct / tool correct | 75.9% / 75.9% of 29 turns |
| Whole turn correct (ASR terms + routing) | 69.0% |
| First audio after the request is processed (median / worst) | 1.61 s / 10.1 s |
| Estimated end of speech to first audio (0.6 s silence wait + ASR + processing), recorded phrases | 3.0 s mean |
| Cost per turn (mean) | $0.0004; shortcut $0.00002, direct $0.00011, tool $0.00015, planner escalation $0.0020 |
| First audio by path (median) | shortcut 1.59 s, direct 1.56 s, tool 2.13 s, escalate 6.47 s |

Aggregate numbers are in `audio_baseline.json`. The per-turn CSV is not committed because it holds the text of your recorded phrases and the replies.

Notes:
- ASR scores worse today than in the 2026-10-27 benchmark on the same recordings (`diagnostics/asr_real/results.json`: 3.6% WER, no missed terms). Decoding here is deterministic (every segment decodes at temperature 0, checked over repeated runs) and uses the same model snapshot, so the cause is not yet explained; it needs a look before the ASR numbers are used to compare models.
- The 9 turns that miss are mostly the same routing gaps as above plus ASR damage: "compared the weather in Shelbyville and Springfield" is not escalated, and phrase 20 (the long NHL/NFL/AcmeCorp summary) was answered directly.
- Typed prompts skip ASR; recorded phrases run through the same headless Julius, so ASR errors carry into routing.

## After the Phase A fixes (2026-10-30)

`audio_baseline.json` now holds the post-fix audio numbers; the first baseline is kept as `audio_baseline_before.json`.

| | Before | After |
|---|---|---|
| Routing eval, all cases | 89.5% (105 cases) | 96.6% (119 cases, majority of 3 runs) |
| direct / escalate / files / followup / intent / tool / utility | 100 / 70 / 76.9 / 100 / 100 / 83.3 / 85.7 | 100 / 70 / 100 / 100 / 100 / 95.7 / 100 |
| New: held-out escalation (10) / parallel lookups (5) | n/a | 100 / 100 |
| ASR mean WER | 8.0% | 3.8% (vocabulary hint) |
| Phrases with a missed term | 3 of 20 | 0 |
| Whole audio turn correct | 69.0% | 82.8% (one phrase relabelled to the new reminder tool) |
| First audio median | 1.61 s | 1.60 s (tool 2.13 to 2.57 s, escalate 6.5 to 9.1 s) |
| Cost per audio turn | $0.0004 | $0.0018 (planner escalations ran longer; single run, noisy) |

Known misses: r097, r101, r105 (Haiku answers with lookups instead of escalating; borderline), r056 (Sunday scores: tool only covers today and yesterday). Six brain cases still vary between runs because Haiku 5.5 accepts no temperature setting.
