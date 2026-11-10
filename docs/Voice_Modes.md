# Julius seed-1301 delivery comparison — September 30, 2026

The selected speaker remains the Qwen 1.7B VoiceDesign seed-1301 identity. The normal runtime uses only `Qwen/Qwen3-TTS-12Hz-0.6B-Base`. The two source recordings were inspected before changing the runtime: balanced is 19.84 seconds, more-lift is 20.64 seconds, both mono 24 kHz. Local Whisper recovered the expected passage from both, with minor interjection/orthography differences.

The original standalone script was found at `test_julius_clone_06b.py` in a Downloads folder. That saved version still contains FP16 and explicit sampling arguments. The user supplied the corrected behavior, which takes precedence: BF16, exact reference transcript, transcript-conditioned cloning, and checkpoint sampling defaults. The existing BF16 output WAVs were retained.

## Comparison

Two approaches generated the same six short lines, covering calm, curious, amused, serious, excited, and calm delivery. Each matched A/B pair used the same seed for controlled sampling; normal runtime does not force that seed. Model, dtype, memory cap, and generation settings were shared. Both approaches' prompts were resident in this comparison process, so this is not a clean separate-process RAM comparison. The individual prompt tensors are small compared with the model.

- A first composite containing both full passages (40.83 seconds) generated five lines, then failed at the 35% MPS allocation cap during the sixth line's audio decoder. The process exited; system swap did not grow. No cap bypass or float32 retry was attempted for this allocation failure.
- A compact composite contains one copy of the reference passage: balanced greeting, lift curiosity/amusement, balanced serious statement, lift closing. It uses consecutive excerpts from the original WAVs, without new speaker synthesis or waveform mixing. Source ranges and SHA-256 are saved next to the composite reference.
- Compact A and two-prompt B both completed all six lines. The guarded comparison took 78.4 seconds, minimum memory-free metric was 24%, and swap did not grow.
- Warm generation median excluding each approach's first line: compact A 5.13 seconds, B 5.64 seconds. Six lines' measured synthesis CPU time: A 9.84 seconds, B 10.21 seconds. Maximum sampled MPS driver allocation: A 4.277 GB, B 4.260 GB. These are small, single-run results; no power draw in watts was measured.
- Generated/reference speaker-embedding cosine similarity remained approximately 0.977–0.992 across these clips. This is a consistency proxy from Qwen's own encoder, not an independent listening evaluation or proof that identities are perceptually identical.
- Whisper recovered substantive content from all twelve compact-comparison clips. Raw word error rates were zero on four of six lines per approach; remaining differences were “I am”/“I'm” and “Ha”/“Huh”. Approximate pitch statistics are saved but cannot establish emotion or listening preference.
- Each approach has a consecutive six-line transition WAV. Naturalness, expressiveness, and perceived transition consistency still need the user's listening review. The data do not establish a perceptual winner.

## Implemented provisional default

B is the default because it directly preserves the original two-mode concept with similar measured resources and latency. Both reusable clone prompts are built once at worker startup, from `1301_a_balanced.wav` and `1301_b_more_lift.wav`, each with the exact supplied `REF_TEXT` and `x_vector_only_mode=False`. Julius already speaks sentence by sentence. The original `TTS_LIFT_HINT` regex is preserved exactly, including question/exclamation punctuation and the original keywords; each sentence selects its prompt. This restores the routing concept, but cloned-reference conditioning is not the same mechanism as dynamically switching 1.7B VoiceDesign instructions.

`JULIUS_VOICE_STRATEGY=composite ./run_julius.sh` selects A for listening comparison. The compact reference is `julius_1301_tuning/1301_composite_reference.wav`. Defaults remain Qwen speech + Ollama Llama 3B, vision off. Original 1.7B scripts and original recordings are unchanged.

Sampling arguments (temperature, top-p, top-k, do-sample, subtalker settings) are omitted, allowing checkpoint defaults. The runtime uses at most twelve words per generation span, preferring clause boundaries, with a maximum of 192 codec tokens (smaller for shorter spans) and startup/speech-worker timeouts; the A/B benchmark used the checkpoint generation-length default. BF16 numerical failure triggers one float32 reload/retry under the same memory cap; memory allocation failures do not trigger a heavier retry. This fallback was tested with injected numerical failure, not an actual hardware numerical fault; all real comparison outputs were finite in BF16.

The updated worker completed a real balanced/lift/balanced/lift sequence in 32.2 seconds with no guard stop or swap growth, minimum free metric 32%. Tests cover exact legacy routing, exact reference text, preservation of sampling defaults, numerical-only fallback, and refusal to reload for allocation failure. Seventeen voice/routing tests passed.

Artifacts: `benchmarks/2026-09-30/voice-modes/`, with compact outputs and measurements under `compact/`. Reference inspection, failed long-composite evidence, successful compact comparison, worker validation, transcripts, pitch estimates, resource histories, and transition WAVs are retained.

## Longer-answer correction and final bounded comparison

Full transcript-conditioned cloning intermittently exceeded the allocation cap during longer generation, even with smaller audio decoder chunks. Clearing allocator cache periodically and whole-text generation did not fix the isolated case; those trial settings were discarded. An early combined run also hit a swap-growth guard when ASR loaded. These are retained as failed tests, not accepted results.

The final implementation keeps the original full balanced/lift reference prompts, uses the native decoder in 96-code chunks with 25-code left context, and generates longer sentences in spans of at most twelve words. All words and the original sentence's chosen delivery prompt are preserved. A 60 ms join separates spans. An allocation failure can retry the failing span as smaller halves, at most three retries per request; it does not raise the cap or load a heavier dtype. Numerical failure alone still permits one float32 retry. This introduces additional phrase boundaries and may affect prosody, which needs listening review.

Before ASR, a used Ollama conversational model is unloaded; Whisper is released after transcription. This reduces overlapping residency, at the cost of a later LLM reload when required. Speech validation now records turn errors and fails the test exit status for fallback turns, avoiding a clean process exit being mistaken for complete success.

Final A/B comparison with the bounded path completed fourteen clips in 114.5 seconds, no guard stop or swap growth, minimum free metric 46%. Warm short-line medians: composite 5.42 seconds, two-prompt 5.74 seconds. Long-answer synthesis: composite 19.16 seconds, two-prompt 18.67 seconds. Short synthesis CPU totals: 10.69 versus 11.01 seconds; long synthesis CPU totals: 6.66 versus 6.46 seconds. Direct power measurement was attempted, but `powermetrics` requires an administrator password; CPU time is only a proxy. These are single-run comparisons, not proof of general performance equivalence.

Results are under `voice-modes/chunked/`. Sampled post-generation MPS driver allocation was roughly 3.17 GB; this is not a peak allocation measurement. Qwen's own speaker-encoder similarity to the selected references remained at least 0.977 for composite and 0.978 for two-prompt. Perceived expressiveness and transition quality remain listening decisions.

The final combined candidate validation (`runtime_validation_adaptive.*`) completed five turns in 86.4 seconds: two conversation replies, a typed Tokyo-time request, a recorded spoken Tokyo-time request, and vision-disabled capability confirmation. All five recorded `error: null`; no guard stop, swap growth, or unrecovered speech failure. Minimum free metric was 28%. These five turns needed no allocation retries; retry logic was verified with injected allocation failure. This remains a bounded recorded-input/synthesis check, with physical microphone and speaker playback pending. Earlier `runtime_validation_final.*` completed its process but included a failed spoken turn, so it is explicitly superseded and not considered a clean pass.

Final A/B ASR recovered every word of both long-answer clips (raw word error rate zero after punctuation/case normalization), including across phrase joins. Joins introduced apparent punctuation/pauses in the ASR transcript; natural prosody still needs listening review. Both final transition WAVs are under `voice-modes/chunked/`.
