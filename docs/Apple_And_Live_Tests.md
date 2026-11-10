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

## Speech comparison update — October 2, 2026

The authorized MLX speech step 1 is complete. Optional `JULIUS_TTS=qwen-mlx`
starts streaming audio in about 0.36 s warm, preserving seed-1301 balanced/lift
cloning. Final combined runtime and reminder restart/cancel tests passed with
devices disabled; 142 regression tests passed. Normal launcher remains Torch
until the user verifies speaker resemblance, excitement and device behavior during
step 2. Actual Mac sleep/wake, physical mic/playback and iPhone notification
acceptance remain pending. See `JULIUS_MLX_SPEECH_TESTS.md` for detailed evidence.

# Apple integration and live diagnostics — October 2, 2026

Julius is connected to the selected iCloud Home calendar and the default iCloud
Reminders list. Account destinations are in private, ignored
`julius_memory/apple_calendar.json`. Do not publish that file or account inventory.
Google remains a future provider option; it is not connected now.

## Implemented and verified

The Apple adapter uses the built-in Calendar/Reminders scripting interface,
passing JSON through stdin rather than embedding user data in scripts or shell
commands. Calendar event previews state the destination; a later valid human
confirmation creates the local event and its Apple copy. Explicit reminder
commands also save to Apple when configured. Tasks currently remain local.

Each local item has a durable UUID operation token recorded before the remote
save. A retry with the same token checks for the existing Apple item. Successful
saves record the remote reference. Timeouts have an uncertain result and are
reported explicitly. Local-only success after a failed Apple save is marked
partial_sync/partial, with a warning that Apple delivery is not confirmed.
There is no automatic background sync retry or Google adapter yet.

Cancellation removes only an exact linked Apple item whose operation marker
still matches. Changed ownership/notes and unresolved saves require review;
they are not silently deleted or called cancelled. Unrelated existing calendar
entries/reminders are not managed. Two requests executing concurrently in
separate Julius instances are not a supported deployment configuration.

Real iCloud tests created a clearly labeled temporary event and reminder,
read back their titles/times, verified the event's start alarm, verified remote
duplicate detection with the same operation token, and removed both owned test
items. All those checks passed. Notifications reaching the iPhone were not
observed: test items were removed before their alert times. Apple delivery still
depends on sync, notification preferences and Focus.

Calendar initial scripting failures were resolved by using verified system app
paths, dismissing Reminders' first-run feature notice, and querying Calendar by
unique name/index. The Calendar identifier conversion failed on this macOS
version, so Home uses a unique name selector; renaming it or creating duplicate
calendar names requires configuration review. Reminders uses its native list ID.
An EventKit compile experiment was abandoned because of the local Swift SDK
conflict; those experiment sources/build artifacts were removed. No system SDK,
privacy database, compiler installation or global power setting was changed.

## Live voice and resource results

The selected Qwen 0.6B Base BF16 voice, original balanced/lift references and
checkpoint sampling defaults remain in use. Vision stayed off.

Actual audio output was exercised for two isolated reminder cases. Delivery,
acknowledgement, planner-object restart recovery and cancellation passed. The
cancelled item was never spoken. The restart case reopened persistent planner
state; it did not restart the whole Mac or the full Julius/voice process.
The listener's judgement of sound and naturalness remains pending.

Voice worker startup took 14.54 seconds. First audio took 14.70 and 16.09 seconds
for the two short reminders; their output lasted 3.76 and 4.16 seconds. This
measured delay is too high for smooth everyday conversation and should remain
an explicit development blocker. These timings are worse than the September 30
short-clip figures; different text/state/conditions prevent attributing the full
change to a specific cause without a controlled comparison.

The live test's memory guard did not stop the process: minimum free metric 27%,
swap increased from 829.62 to a peak of 839.44 MiB (about 9.82 MiB). These are
system-wide metrics, not precise per-model memory accounting.

A seeded CPU-thread experiment compared one thread with the existing four.
Warm two-second clips took roughly 10.5 seconds with either setting; the longer
reminder took roughly 16 seconds. Audio duration and driver allocation were
stable. There was no useful thread-count improvement, so no setting was changed.
Its minimum free metric was 31%, with no swap growth or guard stop. The Mac was
on AC, Low Power Mode was off, and pmset reported no recorded thermal/performance
warning. Actual watts, battery drain and sustained thermal behavior were not
measured. CPU time and RSS must not be presented as energy measurements.

All test model processes were explicitly closed/unloaded after their runs.
No model was added/downloaded, and no extra resident coding model was introduced.

## Combined-runtime memory correction

The first two-turn default-runtime test was stopped by its resource guard:
free memory reached 19%, and swap grew by more than 512 MiB. This was not a
successful stability test, even though the process handled the interrupt.
No Mac restart occurred during these checks.

The candidate now releases the isolated Qwen worker before reasoning-model
inference, collects the response, unloads the reasoning backend, then restarts
the voice worker for playback. It also releases voice before speech recognition.
Direct deterministic tools keep the voice warm because they require no reasoning
model. The same coordination applies to optional vision check-ins, although
vision remains off and that mode was not resource-tested.

The repeated actual two-turn runtime completed with no turn errors or guard
stop: minimum free metric 36%, no swap growth, total run 54.27 seconds. The direct
calculator turn took 12.75 seconds; a five-word conversational greeting took
24.37 seconds. Device input/output was disabled for this combined check; actual
reminder playback was tested separately. No Ollama model remained loaded afterward.

This serialization is a temporary memory-safety correction, not a performance
optimum. Voice startup and response latency remain unacceptable for a seamless
assistant. Streaming/MLX voice benchmarking is now the highest priority. Test
memory directories also isolate file/planner state and Apple configuration via
JULIUS_MEMORY_DIR or JULIUS_STATE_DIR, preventing test runs from sharing real
planner/account state. The pure tests additionally verify that the release hook
precedes model inference and is absent for deterministic direct-tool requests.

## Coding assessment

The launcher uses Llama 3.2 3B for reasoning and Qwen for speech. The balanced and
MLX profiles can use Qwen3 8B for reasoning. Qwen TTS cannot read/write code as a
reasoning agent.

Three small standalone Python functions were generated and checked against
14 executable edge-case examples in a restricted subprocess:
Llama passed 12/14; Qwen3 8B passed 13/14. Both passed ordered deduplication and
first-occurrence binary search. Both failed strict ASCII-only clock parsing;
Llama additionally raised for alphabetic input. Generation was about 2.4–3.6
seconds per function for Llama and 5.5–7.5 for Qwen, with one model loaded at a
time. This is a smoke test, not an unbiased comprehensive coding benchmark,
a repository editing evaluation, or evidence that either model is error-free.
The first verification attempt hit an unsupported resource-limit setting; the
saved outputs were rechecked with the corrected bounded verifier. No additional
inference was required for that correction.

A coding specialist is justified by workflow as well as model ability. It
should preserve code formatting in the UI, use code-specific context, produce
reviewable proposed edits and run meaningful tests in an isolated workspace.
The current spoken persona prompt deliberately favors short spoken prose and
is not an adequate coding-agent interface. No autonomous coding/writes/commands
were enabled. A larger coder should be loaded only on demand and measured against
memory limits, rather than kept resident beside the voice and conversation model.

## Remaining acceptance and focus

135 regression tests pass. Native Apple mutation/readback/cleanup tests and
actual Qwen playback were separately verified. Physical microphone interaction,
OS sleep/wake, iPhone notification delivery and perceived conversational quality
remain pending. The availability question for listening/manual sleep/wake has
not been answered; do not force Mac sleep while the owner may be working.

A supervised sleep/wake check should use a disposable reminder, let the owner
sleep the Mac before it is due and wake it afterwards, then verify one catch-up
announcement, acknowledgement and Apple notification behavior. Document these
as separate observations; a planner restart is not an OS-sleep test.

Recommended next order:

1. Benchmark streaming and the MLX implementation of the same Qwen 0.6B Base
   clone against the current runtime, preserving both selected references and
   checking identity, wording, transitions, memory and first-audio latency.
2. Finish supervised mic, sleep/wake and iPhone alert acceptance.
3. After the user's break, build a small local demo UI with visible listening/thinking/
   speaking/error states, text/code output and stop controls. Acknowledge and
   fix the measured voice delay; do not hide it behind an animation.
4. Add the isolated coding specialist. Then integrate the existing sports models
   and crypto bot through separate read-only research/monitoring adapters first.
   Model versions, data timestamps and evaluated outputs should remain explicit.
5. Package a reproducible, account-free local release for GitHub/portfolio when
   locally deployable. Mobile clients and a Google provider come after that.

MLX Qwen Base cloning support is documented here, but it has not been installed
or benchmarked in this workspace:
https://github.com/Blaizzy/mlx-audio/blob/main/docs/models/tts/qwen3-tts.md
Qwen's separate coding family:
https://qwenlm.github.io/blog/qwen3-coder/

Evidence: `benchmarks/2026-10-02/tests.txt`, `coding.json`, `coding.resources.json`,
`live_reminders/results.json`, `live_reminders.resources.json`,
`voice_threads/results.json`, `voice_threads.resources.json`,
`runtime.resources.json`, `runtime_sequential.resources.json`, and
`runtime/turns_sequential.json`.
The Apple inventory/integration evidence is excluded from Git to protect local
account metadata. Julius is not deployment-ready.
