# Brain benchmarks (2026-10-23, M4 16 GB)
Raw: `benchmarks.csv`; scripts `bench_brain.py` (Ollama) and `bench_mlx.py` (native MLX); logs alongside. Julius's real prompt (`conversation_messages`) and real tool schemas; `num_ctx` 4096; streaming. Machine was not idle (other background jobs, Jupyter, iCloud sync), 48% free at start. n=1 per cell, so treat as ±15%. Julius itself was not launched (microphone/voice phases; see STATUS).

| Model / backend | Cold load | Warm TTFT (chat) | tok/s | Tool-turn first call TTFT | Resident |
|---|---|---|---|---|---|
| llama3.2:3b Ollama (default) | 2.9 s | 0.09–0.22 s | 34–38 | 3.6 s then 0.7 s | 2.3 GB |
| qwen3-4b-instruct Q4_K_M Ollama | 3.1 s | 0.12–0.29 s | 28–30 | 5.0 s then 1.0 s | 2.9 GB |
| qwen3-4b-instruct MLX 4-bit | 1.1 s weights (+~6 s first-use compile/import) | 0.21–0.40 s | 30–35 | 5.3 s then 0.8 s | 2.7–3.0 GB peak |
| qwen3:8b Ollama (balanced) | 5.2 s | not completed | 16 | – | 5.3 GB |

Observations
- **qwen3:8b run was aborted by my guard** at 21% free memory after one turn: on this machine, as loaded, 8B isn't comfortable.
- All backends used the GPU; `keep_alive=0` unloaded cleanly (verified); MLX released to 54 active bytes.
- **Tool prompts are the latency spike**: tool schemas raise the prompt from ~310 to ~1,530–1,790 tokens. First tool turn after a plain turn re-prefills (3.6–5.3 s); warm repeats are <1 s. Julius unloads between phases, so it pays more of these cold costs than my warm loop.
- Generation speed is fine (30+ tok/s ≈ 20+ words/s, faster than speech). The 160-token cap bounds replies to ~5 s worst case.
- Not measured by me (taken from the repo's own live logs, Oct 5–6): voice turn ≈ **8 s** = ASR 2.1 s + brain 3.0–3.4 s + TTS first audio; typed Ollama-Qwen first audio 6.2–7.2 s, native MLX 4.5–4.9 s; calculator route 0.4–0.7 s.

## Bottlenecks, ranked by evidence
1. **Sequential load/unload of ASR, brain and voice** (design choice for 16 GB): each turn re-loads weights (~1–3 s each) and re-primes prompts. Biggest slice of the 8 s.
2. **Tool-schema/system-prompt prefill** (~1.2k extra tokens): 3.6–5.3 s on first tool turn; 0.7–1 s warm. Fix by trimming schemas per intent (only send weather tool for weather-like turns), prompt caching (MLX cache exists but is dropped on unload).
3. **ASR (whisper-large-v3-turbo) 2.1 s** + 1.2 s endpoint silence = >3 s before the brain starts.
4. **Model size**: 8B at 16 tok/s and 5.3 GB isn't viable alongside voice; 3–4B is right for the box.
5. Memory pressure from other apps (swap 2.7–4.5 GB) — environment, not Julius.
6. Not bottlenecks: streaming (on), backend (Metal used), vision (not loaded), history (capped at 8).

## Quality (7 prompts, small sample)
- Chit-chat: all fine; Llama says "functioning properly… How can I assist" (generic, against the persona); Qwen warmer.
- Factual (overfit/underfit): both correct; Qwen more precise, Llama wordy (violates "one or two sentences"). Roadmap documents Llama giving wrong underfitting advice elsewhere.
- Multi-step: both produce markdown lists/"Step 1:" — violates the "no lists" speech rule, so clean_for_speech must paper over it.
- Camera question: Llama "I can… see your text" (muddled); Qwen correct.
- **Tool reliability is the weak point.** Llama: `calculate('17.5 * 2480')` (missing ÷100 → 100× wrong answer if reached; the dispatcher's own calculator route likely intercepts common phrasings). Qwen-Ollama answered 434 without the tool (correct but against the "never compute" rule); MLX answered 433 without the tool (**wrong**). MLX weather call omitted the default location. Weather calls themselves were correct for Llama and Qwen-Ollama.
- Rating (my judgement, n small): accuracy Llama C / Qwen4B B; instruction-following Llama C+ / Qwen B; tool reliability both C+.
