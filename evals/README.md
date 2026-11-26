# Evaluations

Run everything with `python evals/run_all.py`; results land in `evals/results/` (gitignored). Baselines live in `evals/baselines/`.

| Needs | Suites |
|---|---|
| Nothing (offline, free) | file agent (`file_eval.py`), live-data guard, memory commands |
| An Anthropic key (paid, each run prints its cap) | routing, planner, research, coding, live data, prompt injection (`injection_eval.py`: 40 cases × 3 runs; hostile text in pages, files, pictures, the screen, memory, coding tasks and research pages must cause no side effect) |
| Your own recordings | `audio_regression.py`, `asr_*`, `voice_*`, `wake_*`, `bargein_eval.py` read WAV sessions from `diagnostics/` and `voice_data/` (gitignored; record them with `diagnostics/record_wake.py`, `evals/voice_record.py`). The numbers in `evals/baselines/` come from the author's voice and will differ from yours. |
