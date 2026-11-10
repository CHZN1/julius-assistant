## Capture timing and roadmap refresh — October 6, 2026

Implemented microphone arrival timestamps using perf_counter. Main-loop silence/duration measurements now use capture timestamps rather than wall-clock processing times. A capture gap longer than max(2 seconds, twice the configured endpoint) discards partial audio/preroll; backward timestamps also reset. Microphone callback retains copied samples, and calibration reads the updated queue format. Endpoint setting, voice identity, silence gates and model sampling are unchanged. This addresses main-loop backlog timing and interrupted capture; callback scheduling itself and real PortAudio discontinuities still require live validation.

204 regression tests pass; Python compilation and launcher syntax pass. New tests verify contiguous queued capture, natural silent frames, resume/backward discontinuities, actual runtime partial-input reset, and callback timestamps/sample ownership. Evidence: benchmarks/2026-10-06/regression.txt.

Mixed real-ASR/voice/tool/sleep-wake test did NOT reach readiness: guard stopped after 8.39s because system swap grew from 2,896.44 to 3,622.06MiB (>512MiB). Minimum free memory 29%; stopped during voice worker import after native reasoning preparation. No conversation turns completed. Do not describe this as a mixed-pipeline pass or blame a specific app: the guard measures whole-system pressure. Other applications and background jobs were present, but attribution was not established. Preserved logs: mixed_pipeline.log and mixed_pipeline.resources.json. The child process group exited; no microphone was opened in this no-device trial. Do not weaken the guard or repeatedly retry the unchanged workload under current pressure.

Current authoritative priority list: JULIUS_ROADMAP.md. Distinguishes existing foundation from live acceptance, longer reliability including startup pressure, integrated model job coordination, on-demand coder, accessible local UI, and later packaging/mobile/specialist work. Current defaults are unchanged; the native profile remains opt-in. Actual Mac sleep retest and iCloud/iPhone reminder delivery remain pending; preserve the retained test reminder and defer photo work.

Requested availability for a short supervised technical-term/hesitation test; no affirmative availability reply received in this turn. Keep microphone stopped. Next heavy test should begin only with adequate observed headroom, under the existing guard, with the live test explicitly coordinated. Light diagnostics and unit checks can continue independently. Resource-manager integration remains an upcoming milestone, not completed by the capture-clock change.

## Recognition recovery, spoken arithmetic, and conversation reliability — October 5, 2026

Latest request: continue revising and improving the current conversation path. No deployment or default-profile switch. Vision remains off; selected seed-1301 identity, references, sampling, 36-word speech spans, microphone silence gates, and sequential heavy-model phases preserved. All test sessions stopped; microphone is not running.

Changes:
- Both live and scripted audio use recognition recovery. A recognizer exception discards the input, logs its error type, and keeps listening without generating a guessed request or spoken error. KeyboardInterrupt still exits. Rejected microphone turns clear queued audio so backlog is not replayed. Existing scoped ASR cleanup remains in place; recovery tests inject recognizer failures rather than claiming a real GPU-fault endurance test.
- Recognition diagnostics now record segment temperatures. No vocabulary hints, forced deterministic sampling, smaller recognizer, or endpoint-silence reduction was enabled.
- Direct calculator routing accepts basic number words zero through ninety-nine, hyphenated tens, negative operands, mixed numeric/spoken inputs, and existing digit expressions. Fractions, spoken decimals, units, compound arithmetic and larger number words are not covered by this parser. Explanatory statements and unsupported operands are not dispatched by it. Exact calculator evaluation remains bounded and does not use eval.
- Experimental brief conversation instructions favor natural sentences, avoid restating questions and repeated labeled summaries, and discourage absolute slogans. These remain guidance, not guaranteed model behavior.
- Added an explicit camera/voice distinction to the prompt. A real model check still wrongly said camera-off meant text-only; preserved that failed evidence. Narrow known-capability questions now use deterministic answers, bypassing inference/tools. This fixes the sampled question and explicit voice-with-camera-off variants; it is not a general capability understanding guarantee.

Verification: 198 regression tests pass, Python compilation and launcher syntax checks pass. Test log: benchmarks/2026-10-05/latest_conversation_regression.txt. New meaningful regressions cover failure-then-recovery, interrupts, number-word calculations through the actual candidate without model calls, exclusions, and voice capability questions bypassing model/tools.

Recorded recognition checks: both original balanced/lift references plus the existing overfitting audio, each clean and attenuated with light noise. Technical overfitting sentence preserved; reference interjections differed slightly (Ha vs Ah/Oh). Segment temperatures were all zero on the successful default first decoding pass; no fallback-related speed change justified. Six checks completed in 12.14s, minimum free memory 40%, no guard stop. These are synthetic/selected reference fixtures, not the user's microphone, and do not establish underfitting recognition accuracy. Evidence: recognition_robustness.json/.log.

Longer full pipeline: ten typed conversational turns plus assistant sleep/wake, actual audio generation with no playback/microphone, isolated state. Completed in 167.04s, minimum free memory 29%, swap peak 4,446.94MiB with no guard stop, no recorded turn errors. Correctly retained camera-off reason, followed house-price example correction, and used lift only on the celebratory turn. Ordinary typed first-audio delays around 5–6s; longer corrected explanation 7.257s; unrouted word-number math after wake 11.441s. Before-style-revision answers still showed labels, repetition, generic closings and oversimplified explanations. This short run is not an endurance guarantee. Evidence: conversation_reliability.log and conversation_reliability/turns.json. This run loaded the earlier brief prompt; do not attribute its answers to later revisions.

After calculator correction, actual typed-input voice pipeline returned “That's 4.” and “That's 84.” at 0.650s and 0.380s to first generated audio. Calculator dispatcher tasks completed; no brain inference for those turns. Startup preparation still primes then releases the brain before readiness. No microphone recognition or playback in those timings. Guard passed: 14.11s total, minimum free memory 35%. Evidence: spoken_math_pipeline.log and spoken_math_pipeline/turns.json.

Revised style model-only checks maintained the sampled underfitting distinction and follow-ups, but still added generic closing offers and sometimes described camera-off as text-only. The latter now has tested direct routing; broader small-model factual/style limitations remain. Preserve failed outputs at revised_conversation_style/ and voice_context.json/.log.

Next useful live check: technical-word recognition (underfitting, overfitting, regularization), natural hesitation without premature endpointing, and repeated corrections. Current live total latency remains approximately 8s from previous supervised trials; do not claim a general microphone speedup from the new calculator's typed timings. No change to the standard 1.2s endpoint or prior supervised 0.9s setting was made. Physical OS sleep and iPhone reminder sync remain separate deferred checks.

## Native profile listening result — October 5, 2026

User feedback: “Its not too bad in response time now tbh not the most natural but its kind of like someone giving some thought before answering”. This accepts the pause for this short trial, not general deployment or a default-profile change.

One explanatory turn was recorded. Recognition returned “explained over under fitting simply.” The response correctly distinguished overfitting and underfitting, although “underfitting ignores” is an oversimplified closing. No follow-up explanatory turn was recorded, so follow-up accuracy remains untested here. Sleep and wake state transitions were logged, with idle model weights released. No unsolicited explanatory turn was recorded.

End-of-speech to first audio: 7.974s; recognition: 2.091s; processing to first audio: 4.925s; reasoning: 3.389s. This is close to the earlier Llama live result of 7.801s, with different prompts and answers; no controlled full-voice latency improvement is established. Typed-input gains must not be substituted for microphone latency. Native reasoning release reported 116,348 active GPU bytes, not zero allocations.

Session exited cleanly after /quit: 399.03s, minimum free memory 32%, peak swap 4,494.94MiB, no guard stop. This does not establish long-session stability. Microphone session is now stopped. Evidence: benchmarks/2026-10-05/native_conversation_live.log and native_conversation_live/turns.json.

Next priorities: recognition accuracy on technical terms without hallucination-prone vocabulary hints; endpoint timing without clipping speech; longer follow-up/correction and silence tests. Preserve accepted speech flow and seed-1301 identity. Regular launcher default remains unchanged; new profile remains opt-in.

## Conversation quality and backend comparison — October 5, 2026

Current supervised target is the opt-in `mlx-conversation-test` profile. The
regular default remains Llama3.2:3b with Torch Qwen voice; do not silently change
it before the longer acceptance checks. Vision stays disabled. Both test profiles
use the selected seed-1301 Qwen0.6B voice with MLX streaming and sequential model
phases. No coding agent, deployment, Git push, or OS settings change was made.

Implemented this round:

- Permission questions about instructions inside documents bypass both file
  dispatch and model tool selection. The tested question previously triggered an
  unintended read_file call; it now answers without invoking tools. This fixes
  the identified routing cases, not every possible hypothetical or prompt attack.
- Compact conversation receives explicit camera-vision state, preserving user
  corrections and preventing the observed screen-brightness confusion.
- Reply streams close on cancellation. Native backend cleanup releases model,
  tokenizer and prompt cache, and records remaining active GPU allocations.
- Short MLX replies use one output stream across sentence punctuation; retained
  two-reference delivery, 36-word generation spans and microphone silence gates.
- Spoken/typed sleep and inactivity release speech, reasoning and recognition
  weights. Imports can remain warm; no simultaneous heavy-model inference.
- Added `conversation-test` (Ollama) and `mlx-conversation-test` (native MLX)
  profiles with concise replies. Native startup primes imports and the sampling
  path, releases weights, then loads voice before reporting microphone readiness.

Qwen3:4b already installed was Thinking-2507. The new comparison uses the distinct
non-thinking Qwen3-4B-Instruct-2507 checkpoint: Ollama Q4_K_M download about2.5GB,
and MLX4-bit checkpoint about2.26GB under `.cache/julius-mlx-brain`. These quantized
builds share a base checkpoint but are not bit-identical. Existing models and
voice references were preserved; caches are ignored by Git.

Sources: https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507 and
https://huggingface.co/mlx-community/Qwen3-4B-Instruct-2507-4bit .

Broader baseline Llama tests gave wrong underfitting advice and irrelevant Mac
setup advice. Qwen Instruct performed better on those sampled factual and camera
context cases, although answers still sometimes include redundant offers or
oversimplify causes. General accuracy is not guaranteed; longer and adversarial
conversation testing remains necessary.

A stricter30% free-memory experiment stopped during speech startup before warm
brain residency was tested. Two strict30% Qwen-load trials also stopped around
27% free. Do not claim a valid residency comparison. Sequential Qwen-only and
full pipeline trials completed under the existing20% cutoff and512MiB swap-growth
guard with no guard stop. The guards are sampled, not hard OS memory caps.

Ollama Qwen full typed-input pipeline: first audio7.249,6.215,6.163 seconds.
Native MLX initially retained a first-use import/sampling penalty; preparation
was initially not connected to startup, then corrected and regression-tested.
Actual prepared native pipeline: first audio4.902 and4.532 seconds; reasoning
3.299 and3.006 seconds. Minimum free memory34%, no guard stop. Native model release
reported54 active GPU bytes after each turn. These timings exclude microphone,
endpoint silence and actual playback. Do not present them as full voice latency.

Larger512-token prompt batches did not improve reasoning meaningfully and raised
peak allocation slightly; keep the original256 default. True native cancellation
and next-turn recovery passed: cancellation-to-release0.117 seconds,54 active GPU
bytes after both cancelled and recovered turns. Startup priming occurs before
voice construction; a failure still unloads, and readiness does not precede it.

189 regression tests pass. The short supervised native-profile trial is recorded
above; broader follow-up/correction accuracy and reliability checks remain.
Do not repeat iCloud troubleshooting now. Phone delivery remains unverified;
preserve the retained owned Apple reminder. Photo backup/storage cleanup deferred.

Reproduce the candidate with `JULIUS_PROFILE=mlx-conversation-test ./run_julius.sh`.
The native checkpoint must exist in `.cache/julius-mlx-brain`. Existing dependency
runtime is `/opt/miniconda3/envs/torch/bin/python`; no libraries were installed.
Continue core conversation/reliability before adding coding/specialist agents.

## User accepted speech flow and silence behavior — October 5, 2026

The user: "speech flow is much better! and he stays quiet afterwards" after the
36-word-span / no vocabulary-hint / input-gating retry. One actual recognized
utterance: "explain over fitting simply." Correct intent and complete explanation.
No extra user turn logged during silent follow-up. Accepted for this listening
test only; no broad flawless-recognition or long-session reliability claim.

Full pause still7.801 seconds: ASR2.085 seconds, processing-to-first-audio4.767,
reasoning3.332, release.002 seconds. Voiceidentitybalanced,BF16. Guardcompleted
80.55 seconds,minfree33%,peakswap4422.94MiB,no stop. Stopped microphone cleanly.

177 regressiontests pass. Save these flow/input fixes and preserve sequential
model operation. Remaining priority is reasoning startup / total response pause,
then factual quality and longer conversational reliability before specialist
agents or deployment. Diagnostic benchmark_reasoning_startup separates cold-load,
prompt-evaluation and generation timings for4096vs2048 context without audio;
do not change context or residency merely because a microbenchmark is faster.

Reasoning startup diagnostic completed18.14 seconds,minfree39%,no guardstop.
First4096-context request: model load2.088sec,prompt eval.450sec,generation.796sec,
total3.479sec. Later loads vary. Smaller context is not a controlled overall speed
win because generated answers differ and some explanations remain misleading.
No production context/model/residency change made. All test processes stopped.

## Phrase pauses and unsolicited turn corrections — October 5, 2026

The user reported a somewhat faster response, unnatural word pauses and an unsolicited
regularization explanation. Actual guided retry timings: first two questions
8.36 and 7.70 seconds end-of-speech to first audio; 2.30/1.85 seconds ASR and
3.58/3.47 seconds reasoning. This remains slow, not accepted as optimal.

Third recorded "user" turn exactly matched the static vocabulary hint:
"Overfitting, regularization, training data." the user states he said nothing.
Removed ALL initial_prompt vocabulary hints. English default remains, auto
language option retained. Raw Whisper without any hint also hallucinates "Thank
you" on pure zeros with very low no_speech probability. Do not trust its
silence-confidence output alone.

Added raw-audio activity gate before ASR/model loading: reject invalid/nonfinite,
silent/quiet input and isolated clicks; require two sustained frames and >=80ms
of samples over the configured threshold. Preserve short sustained answers.
Capture loop separately requires two consecutive voiced frames; endpoint silence
can no longer qualify a one-frame click. Added microphone gate covering speech
output and 350ms acoustic settling tail, including failure cleanup; callback
drops playback samples. No full echo cancellation or spoken barge-in claim.

MLX speech now uses up to36 words per generation rather than12. 12-word split
placed a boundary between "training" and "data"; independent ASR inserts a full
stop there in old sample, but not new sample. Four guarded identical-sentence
trials: first audio .46–.65 sec;12-word generation8.51–8.65 sec,36-word7.58–8.07
sec; no simulated production stalls. Cumulative worker peak about2.84GB;guard
minimum free43%, no stop. Audio artifacts in benchmarks/2026-10-05/span_audio.
Raw-silence/noise runtime gate rejects in<1ms;both complete samples recognized
through final"unseen data". Tests177pass. Full live acceptance remains pending.
Selected references, identity, BF16/default sampling unchanged. Torch generation
span size unchanged. Keep model phases sequential; still no specialist agents.

## Alternative-brain diagnostic result — October 5, 2026

Installed qwen3:4b metadata identifies Qwen3-4B-Thinking-2507, a thinking
checkpoint. Baseline, soft /no_think and an explicitly closed-thinking-template
trial all failed to provide consistently usable direct answers within the
160-token conversational budget. No alternative brain was promoted. Temporary
julius-qwen3-4b-direct alias was deleted; original models unchanged. The ineffective
backend soft-switch code was reverted. Earlier speculation that template alone
caused the behavior was incomplete; use actual checkpoint metadata.

Final current changes: whole-reply delivery choice, question-mark-only lift
removed, explicit curiosity/amusement/excitement hints retained, guided English
recognition with auto option, and ASR-confidence/reasoning timing diagnostics.
Short-answer prompt experiment reverted due factual error. Llama's factual
reliability remains a known issue; no error-free or deployable claim.
171 regression tests pass (temporary backend-switch tests removed).

## Latest listening feedback and delivery/recognition fixes — October 5, 2026

The user reported improvement but continued excessive pause, initial failure to hear
"overfitting", and an unwanted lift-to-balanced switch. Stopped the microphone
session cleanly. Log shows "or fitting" initially and correct "overfitting" on
retry. This session omitted JULIUS_VALIDATION_OUTPUT, so detailed latency cannot
be reconstructed; do not claim measured full-turn improvement from it.

Delivery is selected once for each complete reply and restored on failure.
Question marks alone no longer select lift; explicit excitement still does.
Both selected references and checkpoint sampling remain unchanged. MLX and
Torch worker protocols both accept the selected reply delivery mode.

Recognition now defaults to English with vocabulary guidance, retaining
JULIUS_ASR_LANGUAGE=auto for automatic language detection. Same synthesized
"Julius, explain overfitting simply" audio was correct in both configurations;
old recognition took 2.24–3.01 seconds, guided English 1.295–1.297 seconds.
This is synthetic-audio evidence, not proof of recognition improvement for the user.

Sequential typed-input voice benchmark (no playback) measured 5.44 and 6.55
seconds to generated first audio, with reasoning 3.97 and 5.14 seconds. End-to-end
microphone delay remains unaccepted. Additional short-answer prompt experiment
again produced incorrect less-training-data advice and was reverted. Comparing
already-installed Qwen3:4b before deciding any model change; Llama remains default.
171 regression tests pass. New detailed logs include reasoning and release times.
No microphone session currently active, no specialist-agent integration yet.

## Conversation feedback and latency corrections — October 5, 2026

The completed three-turn microphone retry FAILED subjective acceptance: the user
reported repeated name-dropping and excessive delay. Logged end-of-speech to
first audio was 14.42, 15.52 and 15.32 seconds. Prevention advice also incorrectly
suggested using less training data. No conversation acceptance is claimed.

Ordinary conversation now uses a compact prompt prohibiting invented personal
traits and third-person narration, preserving history and tool boundaries.
Six automated replies avoided name-dropping and corrected prevention advice;
reasoning took 0.46–2.67 seconds. These are text-only timings, not full voice delay.

Experimental JULIUS_MLX_KEEP_WORKER=1 retains Python imports while unloading
voice model and reference tensors between phases. Three guarded handoffs measured
first audio 0.779–0.861 seconds versus 2.098–2.338 seconds after full restart.
Unload acknowledgments reported 8–315488 active MLX bytes. This does not mean
zero process RSS. Invalid acknowledgments, active streams and cancellation close
the worker. Six trials completed with minimum free memory 38%, no guard stop.
168 regression tests pass. The option remains off by default pending live
ASR/reasoning/voice acceptance. Voice identity and generation defaults unchanged.
Normal silence default remains 1.2 seconds; 0.9 is a supervised trial only.
Continue natural conversation before adding specialist agents. iPhone sync remains
unverified and deferred; preserve the retained Apple reminder.

## Conversation retry and startup correction — October 5, 2026

First microphone attempt (conversation_acceptance) was stopped before readiness
by swap-growth guard: initial free 44%, swap 3521.25 to 4711.31 MiB, minimum free
29%, 6.30 seconds. Preserve failed evidence; no conversational turns occurred.

MLX voice startup constructed/evaluated a waveform-reference encoder, then
removed it. Runtime now uses a scoped decoder-only tokenizer loader because
both selected complete reference codes are already cached and hash/transcript
validated. Decoder weights, checkpoint defaults, seed-1301 references and
balanced/lift routing are unchanged. Installed libraries are not edited; scoped
class/compile hooks restore even on loading failure. Other load_voice_model
callers retain their existing default (encoder-enabled). Real startup plus
cancel/recovery passed under guard: 8.05 seconds, minimum free 51%, cancellation
0.184 seconds, five recovery chunks. Different system headroom means this is
not a controlled paired memory-improvement estimate. 164 regression tests pass.

Retry completed; see the latest feedback and corrections above.

## Pre-agent conversation reliability — October 5, 2026

The user requested finishing conversation/reliability before adding specialists and
confirmed availability for a microphone trial. The prior October 2 natural-
conversation trial has zero recorded turns; its 0.9-second threshold is NOT
accepted or a default. No new coder installation or integration this phase.

Fixed an actual cancellation-handler placement bug: conversational cancellation
now stops without speaking the generic error recovery. Reasoning is unloaded in
a finally block after failed or cancelled Qwen-voice turns. Voice synthesis also
checks reasoning release before loading, so a failed unload yields text instead
of an overlapping voice load. New tests exercise actual runtime functions for
cancel/recovery, unload-before-error-speech, neutral fragment handling and failed
unload preventing voice startup. 163 regression tests pass.

Supervised `benchmarks/2026-10-05/conversation_acceptance` uses isolated state,
MLX seed-1301 voice, Llama3.2:3b, sequential models, vision off, 900-second guard,
and trial silence threshold 0.9 seconds. End-of-speech timing is the detector's
estimate, not an independently recorded phoneme boundary. Pending: actual
conversation turns and human feedback, then audible stop/recovery testing and
revised physical sleep/audio-start recovery. Phone delivery remains deferred
and unverified; preserve the retained Apple test. No deployment or push.

## Natural conversation trial — October 2, 2026

The user confirms the overdue reminder and microphone calculation both worked after
actual Mac sleep/wake. Power log records Wake from Deep Idle at 21:45:55 PDT.
However, the first output stream startup failed with PortAudio -9986; the
existing reminder retry delivered it about 30 seconds later. This is recovered
behavior, not seamless resume. The session exited cleanly; guard minimum free
29%. New output helper retries startup four times with 0.25/0.5/1-second waits,
only before writing audio. It never replays a mid-playback failure. Unit tests
pass; revised physical sleep recovery has not yet been retested.

A stray awake transcription “You” prompted an inappropriate accusation. Exact
incomplete fragments now receive neutral clarification without loading the
reasoning model; short legitimate answers remain accepted. Persona no longer
requires a reaction before each answer and explicitly forbids inferring hostile
intent from unclear speech. Six actual Llama conversational follow-ups finished
in 18.12 seconds under guard (minimum free 44%), including topic recall.

Reminder creation speech omits long reference/caveat narration while retaining
title, due time and Apple partial-sync warnings. Full result/reference remains
on screen. Capture is bounded to 30 seconds. Validation now records ASR time,
processing-to-first-audio and end-of-speech-to-first-audio independently.
156 regression tests pass after the changes.

Active supervised session: `natural_conversation` (900-second guard), isolated
state, unchanged MLX seed-1301 voice, sequential models, vision off. Testing
`JULIUS_SILENCE_SECONDS=0.9` against the existing 1.2-second default. Do not make
the shorter threshold the default until the user assesses natural pauses/cut-offs.
Prompted short coding-session conversation with a ten-minute correction.
Spoken interruption remains unimplemented/unverified; text stop is supported.
Phone delivery remains unverified and deferred until iCloud-space recovery;
leave the retained owned Apple reminder untouched. No deployment or push.

## Current focus correction — October 2, 2026

The user confirms the retained test reminder is absent from iCloud.com as well as
from iPhone. This narrows the unresolved issue to Mac-to-iCloud/account upload;
full iCloud storage is a plausible cause, not proven. Leave the owned test
present and incomplete. Do not proceed to OS sleep testing yet: the user explicitly
states that phase has not started. The supervised microphone session is being
stopped. The local announcement was an ordinary awake delivery, not a sleep/
resume pass. Continue phone/cloud diagnosis before requesting another phase.

## Reliability follow-up — October 2, 2026, evening

The second owned Apple test is intentionally left present and incomplete until
The user confirms phone receipt. Private state: `.cache/supervised-apple-phone-retry-20261002`.
It was due at 21:05 PDT. Native readback confirms one alarm and the configured
list belongs to iCloud (list name Reminders). The user reports it appears only on
Mac despite Reminders enabled and matching-looking lists on iPhone. He reports
full iCloud storage: plausible sync cause, not yet demonstrated. Do not delete
or complete this item automatically. Phone/server delivery remains unverified.

Explicit `complete reminder <name/reference>` and `mark reminder <name> done`
now mark a uniquely owned linked Apple reminder completed without deleting it.
Delivered local reminders can be completed; announcement alone leaves status
`delivered`, never `done`. Ambiguous names and failed Apple operations preserve
local state. Mocked completion tests pass; native completion readback still
needs supervised verification on a separate or human-confirmed item.

The fast ASR/voice overlap is NOT established safe: a clean repeat with no
Ollama model resident tripped the swap-growth guard after 6.59 seconds, with
swap increasing by more than 512 MiB. Preserve this failed evidence. Sequential
loading passed 12 ASR/speech cases in 78.78 seconds with minimum free 42% and
zero swap growth. Median ASR 2.455 s, median speech first audio including worker
reload 2.942 s. These are generated reference clips, not varied live speech.
`JULIUS_MLX_ASR_KEEP_VOICE` remains experimental/off by default.

Text `stop`, `/stop`, or `cancel response` during an active response requests
cooperative cancellation. Playback/generation stops; completed tool actions
are not undone. In-flight nonstreaming tool/network calls finish before the
next cancellation checkpoint. Real MLX cancellation/recovery passed: 0.125 s
at the tested point, next worker generated four chunks. This does NOT verify
spoken interruption or audible playback-stop latency. 152 regression tests pass.

Supervised actual Mac sleep/resume session uses sequential models, isolated
local state in `benchmarks/2026-10-02/supervised_sleep/state`, and 600-second
guard. A due reminder was announced once, but actual OS sleep and resumed
microphone acceptance are pending human feedback and power-log evidence.
Normal launcher remains unchanged; vision off; no deployment or GitHub push.

## Supervised device and latency update — October 2, 2026

The user confirmed physical calculation, assistant sleep and wake controls worked,
but the original response felt slow. He selected the lift reference for
“Great, that worked!”; seed-1301 identity and two-prompt routing remain intact.
Three actual streaming playback clips had zero output underflows. The separate
initial balanced/lift comparison had a synchronous playback-script defect;
the script was corrected and the same saved audio was replayed smoothly.

The Apple test appeared on the Mac, but not the iPhone. Native reminders now
set `remindMeDate` as well as `dueDate`; readback confirmed one alarm with the
correct timestamp. The owned test was deleted. Phone iCloud/notification
configuration and a supervised retry remain pending.

Interactive shutdown now unloads Ollama. ASR uses scoped MLX cache limits and
restores global limits after releasing its model. Keeping the speech worker
loaded during ASR remains opt-in (`JULIUS_MLX_ASR_KEEP_VOICE=1`). An initial
experiment hit the resource guard with a leftover reasoning model loaded;
a clean bounded repeat passed with minimum free 38% and no swap growth.
Live calculator processing after transcription improved from 7.40 to 1.52 s;
The user confirmed the retry felt noticeably faster. This is one paired observation,
not an end-to-end latency distribution or proof of optimal performance.

146 regression tests pass. Vision stays off and the normal launcher remains
Torch pending the rest of supervised validation. The live reminder was marked delivered once; audible acceptance remains
pending. The 358-second retry exited cleanly, with minimum free memory 40%
and no swap growth. Actual Mac OS sleep/resume, phone delivery and longer
latency stress testing remain pending. Assistant sleep/wake does not mean OS sleep/wake passed.
Evidence: `benchmarks/2026-10-02/supervised_*` and
`mlx_asr_keep_voice_bounded/`. No deployment or GitHub push.

## MLX speech comparison — October 2, 2026

Step 1 completed. Optional `JULIUS_TTS=qwen-mlx` preserves the selected seed-1301
speaker and balanced/lift routing with 0.6B Base BF16. Matched first-audio median
0.356 s versus Torch 5.97 s; total generation 3.36 s versus 5.97 s. Speaker
cosine 0.984–0.992. Final two reminders transcribed exactly, restart/cancel passed
with devices disabled. 142 regressions passed. Final combined runtime passed
without swap growth, free minimum 42%, calculator/conversation 2.90/8.83 s.
Normal launcher remains Torch pending supervised acceptance. Initial MLX
allocation failures were guarded and corrected; watts, physical playback,
subjective expression, actual Mac sleep/wake and phone alerts remain unverified.
See `JULIUS_MLX_SPEECH_TESTS.md` and `benchmarks/2026-10-02/` for evidence.

## Combined-runtime correction — October 2, 2026

The original combined Qwen/Llama test hit the resource guard (free 19%, swap
increase >512 MiB). The candidate now releases Qwen voice before reasoning/ASR,
collects the answer, unloads reasoning, then restarts voice; deterministic tools
retain warm speech. Test state uses JULIUS_MEMORY_DIR/JULIUS_STATE_DIR isolation.
The same two-turn repeat passed: no errors/guard stop, free minimum 36%, no swap
growth. Direct calculator took 12.75 s; short conversation took 24.37 s.
135 regression tests pass. This is a temporary memory-safe baseline, not smooth
or deployment-ready. Next priority is streaming/MLX Qwen 0.6B Base cloning speed.
Apple iCloud integration is verified; actual OS sleep/wake, iPhone notifications,
physical mic and listener feedback still need supervised acceptance. No GitHub
push or frontend deployment. See JULIUS_APPLE_AND_LIVE_TESTS.md.

## Apple and live diagnostics — October 2, 2026

Apple iCloud Home calendar and default iCloud Reminders are configured privately.
Real event/reminder creation, readback, remote duplicate detection and owned-test
cleanup passed. Local success with a failed Apple save is explicitly partial.
135 regression tests pass. Actual Qwen reminder playback and planner restart
recovery passed under memory guards, but first audio took 14.7–16.1 seconds.
A seeded CPU-thread comparison did not improve speed; defaults were retained.
Coding smoke test: Llama 12/14 and Qwen3 8B 13/14; both had strict ASCII parsing
failures, so recommend a separately verified coding workflow, not unattended edits.
Physical mic, actual OS sleep/wake, iPhone notification observation and listener
feedback remain pending. Owner availability question was not answered; do not
force sleep. See JULIUS_APPLE_AND_LIVE_TESTS.md and benchmarks/2026-10-02/.
Next: benchmark streaming/MLX Qwen 0.6B Base cloning, finish supervised device
acceptance, then after the user's break build the local demo UI. Sports-model and
crypto-bot agents, local-release packaging and mobile/Google come later.
Julius remains not deployment-ready. Vision stays off. No GitHub repo/push done.

## Utilities milestone — October 1, 2026

Persistent reminders/tasks, dimension-checked units, DST-aware date calculations,
and isolated PDF/DOCX/text extraction/search are integrated into the candidate.
Local calendar events support preview/confirmation, duplicates and overlaps.
External calendar integration is pending the user's provider choice; no account or
external calendar was changed. See JULIUS_UTILITIES.md for commands and limits.
128 tests pass, including actual candidate routing and failure recovery.
Evidence: benchmarks/2026-10-01/utilities/. No heavy models, microphone or voice
playback were loaded for this milestone; physical reminder delivery is pending.
Reminders work while Julius is running, recovering missed items on resume; they
are not OS/background alarms. Vision remains off. Julius is not deployment-ready.

## Dispatcher milestone — October 1, 2026

The candidate now executes direct and model-selected utilities through the
bounded synchronous specialist dispatcher, including trusted human file plans.
98 tests passed; lightweight live news, AAPL quote and MLB scoreboard requests
succeeded. News source/date/URL preservation, market timestamp/currency,
sports tie handling and bounded numeric calculation are implemented.
Read JULIUS_DISPATCHER.md and benchmarks/2026-10-01/dispatcher/tests.txt.
No heavy model or voice/mic test was run for this milestone. Julius remains
not deployment-ready. Active-job cancellation, persistent reminders/jobs and
physical voice acceptance are still pending. Vision stays off.

# Current file-agent milestone — October 1, 2026

The candidate now uses `julius_file_agent.py` (structured scoped service) and `julius_file_session.py` (human-command controller), with improved deterministic routing, shared scoped read tools, and no mutation tools exposed to the model. Normal launcher remains Qwen 0.6B voice + Ollama Llama 3B, vision off.

Implemented: bounded find/list/UTF-8 read; preview/confirm/cancel move, copy, rename, exact-text create; selection fingerprints, collision refusal, current-session/expiry checks, atomic durable journal, output ownership checks, partial-failure reporting, repeated undo and interrupted-operation recovery. Unknown folders and unclear references are clarified, not silently broadened. Details are in `JULIUS_FILE_AGENT.md`.

83 regression tests passed, including 66 file-specific temporary-filesystem tests. The actual candidate turn handler passed preview -> confirm -> undo without invoking a model or opening devices. Synthetic 12,000-file search stopped at its 10,000-entry limit; fifty-file move/undo completed in about 0.17 seconds each. Evidence: `benchmarks/2026-10-01/files/`.

No personal data files were modified by tests. Original runtime scripts and legacy file-history artifacts are preserved; old implicit execution approval is intentionally rejected, so use the candidate launcher for file changes. PDF/Office extraction, semantic indexing, whole-directory moves, mid-operation interactive cancellation, and physical mic/playback acceptance remain outside this milestone. The service is synchronous; dispatcher workers must add task ownership/progress/timeouts/cancellation while keeping confirmation in the trusted human/UI controller.

Next phase: dispatcher integration using the explicit `files` result owner and capability boundary. This milestone is not a deployment-readiness claim.

---

# Current voice correction — seed 1301, balanced + lift

The previous single-reference speaker-embedding implementation has been replaced with two reusable **transcript-conditioned** 0.6B Base clone prompts from `1301_a_balanced.wav` and `1301_b_more_lift.wav`. Exact `REF_TEXT`, `x_vector_only_mode=False`, BF16, checkpoint sampling defaults; original `TTS_LIFT_HINT` routes each sentence. The 1.7B VoiceDesign model does not run in the normal candidate. Numerical failures alone trigger one float32 retry; the existing memory cap remains. Runtime generation uses at most twelve words per span, at most 192 codec tokens, bounded smaller-span allocation retries, and a native contextual audio decoder with smaller chunks. Ollama unloads before ASR to reduce overlapping residency.

A compact composite and two-prompt comparison both completed with no swap growth. Two-prompt is provisional default to preserve explicit delivery routing; listening preference and transition naturalness remain unverified. See `JULIUS_VOICE_MODES.md` for methods, results, failed overlong-composite trial, and listening clips. `JULIUS_VOICE_STRATEGY=composite ./run_julius.sh` selects the compact composite for comparison. Final bounded A/B: warm short medians 5.42 seconds (composite), 5.74 seconds (two-prompt); long answer ~19 seconds for either. Final combined five-turn validation passed with error-aware checking, no guard stop/swap growth, and minimum free metric 28%. Seventeen voice/routing tests passed. Physical mic/playback and subjective listening review remain pending. Previous x-vector-only measurements below are historical and do not validate this updated voice path.

---

# Latest status — Qwen voice enabled, vision off

Testing resumed after the user closed other apps. Normal launch (`./run_julius.sh`) and direct candidate launch now use Ollama `llama3.2:3b` with `Qwen/Qwen3-TTS-12Hz-0.6B-Base`, BF16, cloning `julius_1301_tuning/1301_a_balanced.wav`. Vision stays disabled. This is Qwen speech with a Llama conversational model; Qwen 8B remains an explicit profile choice.

Fresh guarded results are in `benchmarks/2026-09-30/no-vision/`. Warm raw-model median was approximately 1.44 seconds for both Ollama Qwen 8B and cached MLX Qwen 8B, versus 0.45 seconds for Ollama Llama 3B. All three separate comparisons completed with zero swap. These small benchmarks do not establish general model-quality equivalence.

The combined Qwen-voice/Llama-3B candidate completed 15 scripted turns, including live tools, conversation, and recorded audio transcription, without a crash or guard stop. Minimum memory-free metric was 21%; peak swap was 376 MB. Qwen's warm short synthesis took approximately 3.8–4.0 seconds. A subsequent repeated-input trial transcribed all three Qwen-spoken Tokyo-time commands correctly, stayed above 27% free, and grew swap by about 95 MB. Recorded input and synthesis were tested; physical microphone and speaker playback still need a live session. Voice resemblance needs the user's listening judgement.

Safeguards: Qwen runs in an isolated dependency environment/worker with an MPS allocation cap of 35% of recommended maximum, startup/speech response timeouts, and orderly shutdown. Whisper releases its cached model after transcription with Qwen voice. Benchmark guards stop on resource pressure and persist incremental telemetry. The earlier restart's cause remains unproven; passing these bounded trials does not guarantee indefinite stability.

Final worker-response smoke check passed in 16.1 seconds with no guard stop and 49% minimum memory-free metric. Demo: `benchmarks/2026-09-30/no-vision/qwen_final_demo.wav`. Syntax checks and six routing tests passed.

The older paused-test and baseline notes below are historical, superseded by this status.

---

# Current configuration: vision disabled

Vision is parked at the user's request. Normal launch defaults to `JULIUS_VISION=0`; the candidate avoids loading all perception dependencies/models and does not access the camera or run facial-expression check-ins. Voice, typed conversation, live tools, memory, and file routing retain their existing paths. Earlier vision measurements below describe an optional subsystem, not current normal operation. Trained models and face enrollment remain preserved. `JULIUS_VISION=1 ./run_julius.sh` explicitly re-enables it for future work.

---

# Restart incident — testing paused

The Mac restarted during or after the combined custom-voice trial. No Julius test processes survived. A retained kernel-panic report records `watchdog timeout: no checkins from watchdogd in 93 seconds`. This confirms a system watchdog panic was logged, but does not establish that Julius or memory pressure caused the restart the user observed. The custom-voice combined trial did not write its final result and must not be considered stable or accepted. Its earlier isolated voice tests remain valid only within that narrower scope. Testing is paused while the user closes other apps; resume with independent, bounded component tests, not the combined custom-voice stack.

---

# Julius optimization and comparison — September 30, 2026

This is a tested candidate, not an accepted replacement for the original live baseline. Original Julius/B2/B3/B3.1 scripts are preserved. Physical microphone, speaker playback, and live camera acceptance remain pending: the Mac was locked, microphone startup waited, and the original camera worker could not request authorization correctly.

## Earlier baseline recommendation (superseded)

Use the balanced profile for the original Qwen3 8B conversational baseline with faster, grounded live-tool requests and Kokoro. The fast profile uses the already-installed Llama 3B when lower memory and latency matter more. The custom-voice profile pairs Llama 3B with the 0.6B clone of the existing seed-1301 balanced recording. The MLX profile is now viable with prompt caching; there is no sufficient evidence to declare it universally better than Ollama.

Run from this project in the existing `torch` environment:

```sh
./run_julius.sh
JULIUS_PROFILE=fast ./run_julius.sh
JULIUS_PROFILE=custom-voice ./run_julius.sh
JULIUS_PROFILE=mlx ./run_julius.sh
```

The launcher now defaults to custom-voice. Explicit JULIUS_BACKEND, JULIUS_MODEL, and JULIUS_TTS override a profile. A new voice reference can be set with JULIUS_VOICE_REFERENCE. The clone uses speaker embedding mode, so its perceived resemblance still needs the user's listening review.

## Changes

- Shared lazy backend adapter, with no startup generation. Ollama keep-alive reduced from 24 hours to five minutes; context defaults to 4096 tokens.
- MLX reuses a bounded prompt cache (one entry, at most 768 MiB), limits transient allocator cache to 256 MiB, and uses smaller prefill batches. A 256 MiB prompt-cache trial was ineffective because it could not retain the tool prompt.
- Explicit live weather, forecast, datetime, calculator and Bitcoin routing. Springfield defaults and location follow-ups retained. Answers come directly from real tool output rather than another model pass. Failures remain failures.
- Tomorrow answers use tomorrow's row; time-difference follow-ups give the difference; arithmetic answers avoid reading a stripped multiplication expression as one large number.
- Non-shortcut tool selection is deterministic, permits 256 tokens instead of 60, and is capped at two passes. Ordinary final generation is limited to 160 tokens. Stall detection catches short promises before speaking them. Invalid/truncated MLX tool JSON raises a handled error.
- Microphone queue bounded to 30 seconds. Turn exceptions are reported and handled rather than ending the runtime.
- Candidate vision runs at 2 FPS, with emotion inference at most once per second. Smoothing count follows the original approximate smoothing duration. Only detection and identity InsightFace models are retained. Original Perception defaults remain available to older scripts.
- Camera opens on the main thread to permit macOS authorization; unavailable capture is explicitly reported. Vision thread is joined on stop.
- Optional Qwen 0.6B clone uses BF16 and an isolated speech worker, avoiding the 1.7B float32 VoiceDesign runtime. The worker closes on normal shutdown.
- Persistent memory staging and file-action approval/undo flows are preserved. Validation uses separate temporary memory and skips extraction/review, so benchmark turns do not become personal memories.
- Timezone tool retains fractional-hour offsets.

## Measurements

Raw evidence is in `benchmarks/2026-09-30/`. Model tests used the same original system prompt, nine questions, deterministic sampling, and a 256-token limit. Each completed run had two rounds. Warm medians below exclude the calculator request, which switches to a substantially different part of the tool prompt, and exclude first-round cold startup. They are request-completion times, not time to first token or speech latency.

- Ollama Qwen3 8B: warm median 1.42 s; 12/18 raw-model cases had valid tool arguments/concise answers. The failures involved unresolved `your location` defaults.
- Original uncached MLX 8B adapter: warm median 10.53 s; 12/18 valid cases.
- Corrected MLX cache: warm median 1.64 s; 12/18 valid cases. Active MLX allocation was about 4.93 GB, plus about 0.27 GB allocator cache. Model residency in the Ollama combined run was about 5.30 GB. These counters have different meanings and should not be added to process RSS.
- Ollama Llama 3B: warm median 0.44 s; 16/18 valid cases. It invented Barcelona for the unspecified forecast. Candidate routing prevents this error for the tested clear request. Model residency was about 2.33 GB.
- Installed Qwen3 4B is actually Qwen3-4B-Thinking-2507. It reasons despite `think=False`, spends the token budget on reasoning, and is unsuitable for the current voice flow. Trials stopped early. A temporary template variant did not solve this and was deleted.
- Six-turn conversation sequence: warm completion median 3.23 s for Qwen 8B and 1.44 s for Llama 3B. Both retained the stated project and overfitting context. This small sequence does not establish general model-quality equivalence.
- Kokoro generated ~2.7-second clips in about 1.15–1.30 s.
- BF16 Qwen clone generated ~2.9–3.2-second clips in 6.52–7.29 s warm, with 11.23 s on the first sample. MPS active allocation was about 2.20 GB, driver allocation about 3.27 GB. Separate-worker startup was ~13 s, and its sample took ~6.74 s.
- Whisper correctly transcribed all six generated voice samples. The reference transcript was also recovered.
- Recorded face-present vision workload, 20-second samples: original 8 FPS used 33.83 CPU-seconds; 2 FPS/one-second emotion interval used 9.48; restricting retained InsightFace modules used 6.04. Final reduction: approximately 82%. The user's identity confidence (~0.8265) and neutral-expression confidence (~0.4698) remained consistent on this static recording. This is CPU work, not a measured wattage or battery-life improvement.

The Mac has 16 GB unified memory and already had about 39.7 GB swap in use before testing. Background workload, swap, model load order, and differing generated replies affect these timings. No fresh model-weight downloads were necessary. The historical crash cause is not conclusively identified. The old 1.7B float32 voice adds a substantially larger memory requirement than the tested 0.6B BF16 clone, but it was not deliberately relaunched to reproduce a crash.

## Combined-runtime validation

Both Ollama Qwen 8B/Kokoro and corrected MLX 8B/Kokoro completed 14 scripted turns with live tools, actual speech synthesis, recorded-face vision, conversation follow-ups, Whisper transcription of recorded audio, and isolated persistent transcript logging. Both exited cleanly without a crash. All eleven tool/follow-up cases also passed a fresh live-service check after final routing refinements. An injected tool timeout remained grounded and triggered zero model inference.

The first combined conversation turn took 40.46 s for Ollama and 16.10 s for MLX; the following conversation turn took 7.73 s and 7.58 s respectively. These include speech synthesis and contention with perception, omit physical playback, and are single observations rather than a stable cold-start ranking.

Custom voice combined-runtime results are saved separately. Physical capture/playback and a live face are still required before treating any candidate as the accepted everyday baseline. Long-duration stability and power in watts remain unverified.

## Reproducing key checks

```sh
python test_julius_routing.py
python test_julius_candidate.py
JULIUS_BACKEND=ollama python benchmark_julius.py --output benchmarks/ollama-repeat.json
JULIUS_BACKEND=mlx python benchmark_julius.py --output benchmarks/mlx-repeat.json
JULIUS_BACKEND=ollama JULIUS_MODEL=llama3.2:3b python benchmark_conversation.py
JULIUS_TTS=kokoro python benchmark_tts.py
JULIUS_TTS=qwen-clone python benchmark_tts.py
python benchmark_asr.py
```

Qwen voice dependencies are isolated because the installed MLX package requires Transformers 5+, while Qwen-TTS 0.1.1 requires 4.57.3. To recreate the voice worker environment from `torch`:

```sh
python -m venv --system-site-packages .venv-qwen-tts
.venv-qwen-tts/bin/python -m pip install -r requirements-qwen-voice.txt
```

Recorded vision profiling requires the temporary enrollment-image conversion used in this session; `profile_perception_recorded.py` clearly labels that workload. Validation-only JULIUS_VALIDATION_SCRIPT, JULIUS_VALIDATION_NO_DEVICES, JULIUS_VALIDATION_OUTPUT, JULIUS_CAMERA_SOURCE and JULIUS_MEMORY_DIR allow repeatable scripted checks. Do not set validation overrides during ordinary use.
