# Bring your own model

Every model Julius uses goes through one provider interface (`julius/brain/providers/`), so each tier can run on Anthropic, on a local model, or on any OpenAI-compatible endpoint. With no `[models.<tier>]` blocks nothing changes: Anthropic, with `[models] haiku` and `sonnet`, exactly as before.

## Tiers

| Tier | What it does | Default |
|---|---|---|
| `front_end` | Every voice or typed turn starts here: answers, tool calls, escalation. Also conversation titles and summaries, and memory auto-capture. | Anthropic, `[models] haiku` |
| `planner` | `escalate_to_planner`: plans, comparisons, briefings, file clean-ups (proposals only), and page rewrites. | Anthropic, `[models] sonnet` |
| `research` | The research sub-agent (`research_task`). | Anthropic, `[models] haiku` |
| `vision` | Camera, screen, dropped pictures and page photos. Needs a model that accepts images. | Anthropic, haiku or sonnet per request |
| `coding` | The coding agent. | `claude -p` with `[code] model` (other providers: see below) |

## Providers

| `provider` | Talks to | Default `base_url` | Key |
|---|---|---|---|
| `anthropic` | Anthropic Messages API | (SDK default) | `ANTHROPIC_API_KEY` |
| `ollama` | Ollama's native `/api/chat` | `http://localhost:11434` | none |
| `ollama_v1` | Ollama's OpenAI-compatible `/v1` | `http://localhost:11434/v1` | none |
| `lmstudio` | LM Studio's server | `http://localhost:1234/v1` | none |
| `llamacpp` | llama.cpp `llama-server` | `http://localhost:8080/v1` | none |
| `vllm` | vLLM's OpenAI server | `http://localhost:8000/v1` | none |
| `openrouter` | OpenRouter | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `openai_compat` | any other chat-completions endpoint | set `base_url` | set `api_key_env` if it needs one |

## Configure a tier

In `julius.toml`, or in `julius.local.toml` to keep it to your machine:

```toml
[models.front_end]
provider = "ollama"
model = "qwen3:4b-instruct-2507-q4_K_M"
# base_url = "http://localhost:11434"    # each provider has a default
# api_key_env = "MY_ENDPOINT_KEY"        # the NAME of the environment variable, never the key
# price_in = 0.0                         # USD per million tokens, for the cost ledger (local models cost $0)
# price_out = 0.0
# timeout = 120                          # seconds
# options = { num_ctx = 16384 }          # Ollama options, or extra chat-completions fields
```

Tiers you don't list keep their default. A bad block (unknown provider, missing model, missing `base_url`, a URL that isn't http or https) stops Julius at start-up with a message naming the block.

### Working example: the front end on Ollama

```bash
ollama pull qwen3:4b-instruct-2507-q4_K_M
```

Add to `julius.local.toml`:

```toml
[models.front_end]
provider = "ollama"
model = "qwen3:4b-instruct-2507-q4_K_M"
```

Then start Julius as usual. Every turn now runs on your Mac; the planner and research still use Anthropic unless you move them too. To try it without editing a file:

```bash
JULIUS_TIER_FRONT_END="ollama:qwen3:4b-instruct-2507-q4_K_M" python -m julius
```

`JULIUS_TIER_<TIER>` (`provider:model`), `JULIUS_TIER_<TIER>_BASE_URL` and `JULIUS_TIER_<TIER>_OPTIONS` (JSON) override the file for one run. With no Anthropic tier left, no Anthropic key is needed.

## What each provider can do

| | Anthropic | Ollama native | OpenAI-compatible |
|---|---|---|---|
| Tool calling | yes | yes (model-dependent) | yes (model- and server-dependent) |
| Forcing a tool (`tool_choice`) | yes | no: becomes an instruction in the prompt | yes, unless `options = { forced_tool_choice = false }` |
| Streaming | yes | yes | yes |
| Pictures | yes | with a vision model and `options = { vision = true }` | with a vision model and `options = { vision = true }` |
| Prompt caching | yes (explicit) | automatic prefix reuse | server-dependent (reported as cache reads when the server says so) |
| Cost ledger | Anthropic prices | $0 | `price_in` / `price_out` |

Every cost-ledger row (`julius_memory/costs.jsonl`) records the `provider` and `model` that answered.

## Measuring a provider

```bash
python evals/run_all.py --provider ollama --model qwen3:4b-instruct-2507-q4_K_M --tier front_end --workers 1
```

This runs the routing, file-agent, live-data, memory, planner and research evals with that tier replaced (`--skip planner,research` leaves out the two that measure other tiers). Every eval CSV and summary gains a `provider` column, and the run writes `evals/results/providers_<date>.csv`. Use `--workers 1` for a local model: one request at a time keeps its prompt cache warm (about 3 s a turn instead of 11 s on an M4).

A provider passes a tier only at these floors:

| Floor | Value |
|---|---|
| Routing | ≥ 95% (Anthropic: 100% on 2026-11-24, 267 cases × 3 runs) |
| File agent | 100% (no unsafe action) |
| Tool-call JSON validity | ≥ 99% |
| Memory eval | 6/6 sections |
| Live data | every figure comes from a tool call made in that turn |

### Measured

| Date | Tier | Provider and model | Routing | Tool JSON | File | Live data | Memory | Verdict |
|---|---|---|---|---|---|---|---|---|
| 2026-11-24 | front_end | anthropic `claude-haiku-5-5` | 100% (267, ×3) | n/a | 100% | | | passes |
| 2026-11-24 | front_end | ollama `qwen3:4b-instruct-2507-q4_K_M` (M4, 16 GB) | see below | | | | | |

## Known weak spots of small local models

- **Long prompts.** Julius's system prompt and tool list are about 13k tokens. Ollama's own default context is much smaller and silently cuts the prompt, so the adapter asks for 16,384 (`DEFAULT_CONTEXT` in `julius/brain/providers/ollama.py`); raise it with `options = { num_ctx = ... }` if you add tools.
- **Forced tools.** Ollama can't force a tool call, so live-data questions rely on the prompt saying "answer by calling a tool". Check the live-data eval before trusting a model with scores, prices and times.
- **Tool-call reliability.** Small models sometimes answer in prose where a tool was needed, or pick a near-miss tool. The routing eval's misses show which categories.
- **Speed.** The first turn after loading pays for the whole prompt (about 11 s for a 4B model on an M4); later turns reuse the cached prefix.

## Coding tier: plan (not built)

`claude -p` stays the coding agent for now. A non-Anthropic coding model needs Julius's own edit/test loop, so the safety rules don't depend on one vendor's CLI. The plan, keeping every existing layer in `julius/agents/code_agent.py`:

1. **Same sandbox, new driver.** The task copy, the fresh git repo with no remote, the seatbelt profile, the scrubbed environment and the merge-back step stay as they are. Only the process inside the sandbox changes: a small Julius runner (`python -m julius.agents.code_loop`) instead of `claude --bare`.
2. **Tools inside the runner.** `read_file`, `list_files`, `search`, `edit_file` (exact replace, like Claude Code's Edit), `write_file` and `run` (tests and builds only). Every path is resolved inside the task folder. `run` refuses the same deny-list as today (git push, remotes, curl, ssh, rm, package installs) and has a timeout.
3. **Network.** The egress proxy's allowlist becomes per task: the tier's endpoint (for example `localhost:11434` for Ollama) instead of `api.anthropic.com`. Nothing else is reachable.
4. **Loop limits.** Step cap, wall-clock timeout and the per-task dollar cap (from `price_in` / `price_out`, or $0 locally), checked before every call, as `claude -p` is today.
5. **Evals.** `code_eval.py` gets `--provider` like the others, with its own floor: no sandbox escape ever (the existing escape cases), and a pass rate set from the first measured run.

Estimate: about 1.5 weeks (the loop and tools 3 days, sandbox and per-task proxy 2 days, the evals and escape tests 2 days, docs 1 day). That is over the one-week line, so the provider work for the other tiers ships first and this waits for its own slot. Small local models are also weak at multi-file edits today, so the measured value of this tier would be low for now.
