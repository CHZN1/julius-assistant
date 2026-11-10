# Julius MLX speech comparison — October 2, 2026

Step 1 is complete: the MLX 0.6B Base speech candidate is integrated and ready
for supervised acceptance. The ordinary launcher remains on the established
Torch worker until the user checks the actual voice, expression and device behavior.
Julius is not deployment-ready. Vision remains off.

## Result and choice

The matched comparison used the same complete seed-1301 balanced/lift WAVs,
exact REF_TEXT, transcript-conditioned cloning, BF16 and checkpoint sampling
settings. Three texts were repeated twice for each of full audio, 0.32-second
streaming and 0.8-second streaming. Torch was tested separately on the same
texts; the two runtimes have different random generators and produced different
audio durations, so these are matched workloads, not identical waveforms.

- Torch median complete/first audio: **5.97 seconds**; startup **9.08 seconds**.
- MLX 0.32-second chunks: median first audio **0.356 seconds**, complete
  generation **3.36 seconds**. Median audio duration 4.64 seconds, versus
  Torch 3.44 seconds.
- MLX 0.8-second chunks: first audio **0.669 seconds**, complete **3.30 seconds**.
- MLX full audio: first/complete audio **5.24 seconds**.

Choose 0.32-second streaming for supervised tests. The small total-generation
advantage of 0.8-second chunks does not compensate for its later first audio.
No quantization or speaker redesign was needed. Median CPU time was 1.75 seconds
for streaming versus Torch 1.98 seconds; this does **not** measure watts,
battery drain or total system energy.

Official Qwen speaker-embedding cosine scores for the MLX clips were
**0.984–0.992** against the matching selected reference. This supports speaker
consistency; it does not establish that the user will prefer the sound or excitement.
Automated chunk supply checks showed no playback supply deficit in five worker
cases, including a long response. Twelve streamed WAVs had no chunk-boundary
step larger than their whole-file 99th-percentile adjacent-sample step.
Physical playback continuity remains a supervised check.

## Corrections and safeguards

Initial stock MLX trials stopped safely on >512 MiB swap growth during reference
encoding and full decoding. These failures remain recorded. A subsequent
compatibility check found that MLX Audio 0.5.7 wraps the decoder with mx.compile,
removing access to its streaming methods. The local adapter preserves the loaded
decoder module and evaluates full-decoder chunks separately, with 96 codec
tokens and 25 tokens of left context. It does not edit installed package files.

The official Qwen encoder now exports both complete reference-code prompts once
to .cache/julius-mlx-prompts. MLX checks reference SHA256 and the exact transcript
before reusing them. This avoids its expensive full-reference encoding path.
The worker drops the now-unused encoder weights after prompt loading. Final
reminder tests measured about **2.29 GB active MLX allocations**, **2.84 GB peak**.
These allocation counters are not interchangeable with process RSS or Torch
driver counters.

The public MLX cloning entry point raises repetition penalty from the checkpoint's
1.05 to 1.5. The pinned adapter uses its explicit ICL method with checkpoint
temperature/top-k/top-p/repetition settings. It retains the original
TTS_LIFT_HINT routing and bounds generation to twelve words per span and at most
192 codec tokens. No 1.7B VoiceDesign model runs in the worker.

The worker emits ordered audio chunks followed by completion, with bounded
temporary files, validation, response/utterance deadlines, and cleanup. Abandoning
or failing a stream closes its worker so stale messages cannot enter another
request. Playback uses one continuous output stream rather than restarting the
device for every chunk. Reasoning/ASR and voice still release one another's
weights; simultaneous model residency has not been adopted.

MLX sometimes omitted the opening spoken label "Reminder" with a colon, period
or comma. A complete introduction, **"Here is your reminder."**, preserved the
introduction and content in six repeated wording checks. The two final real
planner-delivery recordings both transcribed exactly, ignoring punctuation/case.
The normalization affects only speech beginning "Reminder:"; reminder data,
printed results and the reference recordings are unchanged. Number spelling
("ten" versus "10") explains the raw ASR word-error entries in the wording test.

## Validation

142 regression tests passed, including streaming backlog handling, interrupted
consumer cleanup, empty/error responses, reminder normalization, one output
stream for multiple chunks, and sample-rate changes. Actual worker cancellation
and restart, style switches, long speech, planner restart catch-up, repeated
delivery prevention and cancellation passed with devices disabled.

The initial corrected 18-case comparison passed the resource guard, with about
81 MiB swap growth and minimum memory-free metric 29%. Streaming-only worker
tests, paired Torch tests, final reminder delivery and actual combined Julius
turn tests passed without additional swap growth. See the incremental telemetry
for exact conditions; passing bounded tests is not indefinite stability evidence.
The final two-turn run completed calculator/conversation turns in **2.90/8.83 s**,
with free minimum **42%**, versus the earlier Torch run's **12.75/24.37 s** and
36%. These are the same prompts, but the conversational answers differ.

Evidence is under benchmarks/2026-10-02/: mlx_decoder_fixed/, voice_paired/,
mlx_worker/, mlx_audio_checks/, mlx_audio_checks_paired/,
mlx_natural_wording_asr/, mlx_reminders_verified/,
mlx_reminders_verified_asr/, mlx_runtime_final/ and mlx_regression_tests.txt.
Earlier failed attempts are mlx_voice.log, mlx_cached.log and mlx_bounded.log.

## Run the candidate and supervised step 2

Use `JULIUS_TTS=qwen-mlx ./run_julius.sh` for the supervised session. The ordinary
launcher still selects Torch. `JULIUS_TTS=qwen-clone ./run_julius.sh` explicitly
selects the previous worker.

For a fresh setup, create .venv-mlx-tts with Python 3.12, install
requirements-mlx-voice.txt, download
mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16 with HF_HOME set to the project's
.cache/huggingface-mlx-tts, then run prepare_mlx_voice_references.py with
.venv-qwen-tts/bin/python under run_guarded_test.py. Existing local setup is ready.
The isolated MLX environment does not alter the Torch/Transformers environment.

During step 2, check balanced/lift speaker resemblance, genuine excitement,
sentence transitions, physical microphone wake/sleep commands and repeated
conversation. Then check actual reminder playback, restart catch-up, Apple/iPhone
notification delivery, and actual Mac sleep/wake while the user is present. Observe
memory and response time during the session. No microphone, speaker playback,
personal reminder creation or OS sleep was performed in this step-1 comparison.

After acceptance, select the final default and take the planned break before
building the local demo frontend. Coding, sports-model and crypto integrations,
release packaging, GitHub and mobile/Google remain later work.
