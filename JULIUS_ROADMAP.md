# Julius roadmap

Updated October 6, 2026. Development candidate; not yet locally deployable.
This document is the current priority order. Older milestone documents retain historical results, not current readiness claims.

## Current direction

Finish dependable conversation and resource handling before adding another model agent. Preserve the seed-1301 Julius identity, balanced/lift delivery, and camera-off operation. Compare models using the same requests and full voice timings before choosing a default.

The distinctive product direction is an assistant whose actions are understandable and verifiable: scoped specialist ownership, previews before changes, undo where supported, source/timestamp receipts for live facts, and visible model handoffs on limited hardware. These are concrete strengths to develop and demonstrate; they are not claims that no other assistant has them.

## Foundation already implemented

- Scoped deterministic file service: bounded search/read, move/copy/rename/exact-text creation, preview and human confirmation, selection fingerprints, collision protection, interruption journal, and ownership-checked undo. The NHL model-file organization was executed and hash-verified. Broader everyday file UX still needs acceptance testing.
- Dispatcher: explicit specialist ownership, argument validation, bounded per-turn calls, task/result metadata, and deterministic direct routes for supported requests. These services are not additional resident language models.
- Utilities: bounded calculator, conversions, dates/time, weather, sports, quotes, news/search, document extraction, local tasks/reminders, and Apple calendar/reminder integration. Live provider coverage and failure semantics vary; news search snippets are not verified full articles.
- Voice: Qwen 0.6B Base cloning with selected seed-1301 references; balanced/lift routing per whole reply; MLX streaming candidate with longer generation spans. User accepted improved flow and silence afterward in short trials.
- Sequential speech recognition, reasoning, and voice weight release; idle/sleep release; text cancellation and recovery; output-device startup retries. Resource guards sample memory/swap/disk and can stop tests. They are not hard OS memory caps.
- Narrow factual capability answers and permission-discussion routes bypass model guesses. Spoken-number arithmetic uses the calculator directly for its supported grammar.

## 1. Finish conversation acceptance — active

Check the user's microphone on underfitting, overfitting, regularization, short answers, natural hesitations, corrections, and silence after playback. Capture timing now uses microphone arrival timestamps; verify that in live operation. Keep the 1.2-second standard endpoint until a shorter setting passes hesitation checks.

Use identical prompts to compare the current default (Llama3.2:3b + Torch voice) with the opt-in native Qwen3-4B-Instruct + MLX voice profile. Judge recognition, answer correctness, consistency of speaker/delivery, full end-of-speech-to-audio delay, and memory together. The last supervised general reply was approximately 8 seconds; typed calculator timings around 0.4–0.7 seconds exclude recognition.

Exit evidence: repeated supervised turns with accurate technical terms and follow-ups, no clipped pauses or unsolicited replies in the tested conditions, and a documented profile decision. A short positive listening test is not general accuracy or endurance proof.

## 2. Verify runtime reliability — active automated work

October 6 mixed-pipeline attempt stopped during startup when whole-system swap grew by more than 512 MiB. No turns completed. Address startup headroom before another heavy run; preserve the guard and failed evidence. 204 regression tests pass, but the updated capture timing still needs a live check.

Exercise mixed ASR/reasoning/tool/voice phases, repeated assistant sleep/wake, cancellation, failed recognition, device startup failure, unavailable providers, and safe shutdown. Extend to a guarded 15–30-minute session after shorter tests pass. Monitor memory trend and swap growth rather than a single peak; test realistic other-app load only with supervision.

Retest actual Mac sleep/resume separately. Previous reminder and calculation eventually worked after resume, but output-device startup failed first; revised retry and capture-discontinuity handling have not passed another physical sleep trial. Assistant “go to sleep” tests do not satisfy this gate.

Exit evidence: no unexplained turn loss, duplicate actions/announcements, leftover heavy models, or growing resource trend in the tested duration; clear recovery and visible failure reports. Fix failed checks before expanding the workload.

## 3. Integrate the model job coordinator

Connect the existing ModelResources foundation to real runtime owners before loading a coding model. Julius accepts the request, releases voice/recognition/brain weights, checks headroom, runs one specialist job under a guard, unloads it, then resumes conversation. Unload failure must prevent the next model from starting. UI-visible progress and text cancellation must remain available during a job.

Exit evidence: measured handoff, pressure refusal, timeout/cancellation, cleanup failure, and next-turn recovery with actual resident models. The registry exists but is not yet an integrated runtime memory manager.

## 4. Add a bounded coding specialist

Benchmark an appropriate local coder only after the coordinator passes. Start with explicitly scoped repository reading, explanation, and proposed diffs. Require reviewable changes, meaningful tests, and explicit approval for execution or publishing outside the authorized scope. Do not assume a model's download size is its runtime RAM requirement.

Keep file operations with the file specialist; coding owns code analysis and patch proposals. Begin with one on-demand specialist, not several simultaneously resident models.

Exit evidence: correct small-repository tasks, bounded context, actual memory/latency measurements, safe cancellation, actionable errors, and restoration of Julius voice. No coder model is installed as part of the current milestone.

## 5. Build the local demo interface

Show idle/listening/thinking/speaking/sleep states, recognized text, response text, stop/cancel, microphone controls, and clear failure messages. Make file previews and exact results understandable without terminal knowledge. Use readable text, keyboard navigation, accessible controls, and concise confirmations.

Show action receipts with specialist owner, what changed, source/timestamp for live facts, and undo availability. Keep implementation/model settings in an optional advanced area. Measure usability with realistic tasks rather than adding UI decoration first.

Exit evidence: complete conversation and file/reminder workflows through the UI, accessible navigation, and no need to interpret terminal logs for ordinary tasks.

## 6. Local release and portfolio package

Only after acceptance: document requirements and supported commands, reproducible setup, model/source licenses, bounded resource expectations, recovery procedures, and known limitations. Verify installation from a clean environment. Exclude personal files, caches/model weights, reminder state, tokens, and private benchmark transcripts from publication.

Prepare a demo and GitHub-ready repository when requested. Deployment/publishing are final steps, not current actions.

## Deferred work

- iPhone reminder delivery: Mac creation was verified, iCloud/iPhone sync was not. Full storage is a plausible contributor, not a confirmed diagnosis. Preserve the existing incomplete test reminder. Revisit after the planned storage/backup work; do not assume delivery succeeds.
- Bulk photo backup to the external drive and iCloud cleanup: separate supervised task near the end; no photo deletion authorized here.
- Google calendar/reminders for cross-platform use, mobile app architecture, and Android/iPhone clients: after the local runtime and UI stabilize.
- Sports prediction/EV models and crypto-bot monitoring: later bounded specialist integrations with clear read/management permissions, sources, and measured model resources. No autonomous betting or trading is implied.
- Vision: remains off. Revisit only with a concrete useful capability and separate perception/resource validation; robotics navigation is a separate project.
- Symbolic math, richer document generation, and additional specialist agents: add for demonstrated tasks after ownership and resource handoffs are stable.

## Evidence to keep distinct

Regression tests verify supported code paths, not universal model accuracy. Synthetic audio is not the user's microphone. Typed first-audio measurements are not full voice latency. Assistant sleep is not Mac sleep. Mac reminder creation is not phone delivery. A resource-guard pass is not a crash guarantee.

See CODEX_HANDOFF.md and JULIUS_TEST_RESULTS.md for the latest measured checks and preserved failures.
