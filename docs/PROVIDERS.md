# Bring your own model

Every model Julius uses goes through one provider interface (`julius/brain/providers/`), so each tier can run on Anthropic, on a model you serve yourself, or on any OpenAI-compatible endpoint. With no `[models.<tier>]` blocks nothing changes: Anthropic, with `[models] haiku` and `sonnet`, exactly as before.

**Who this is for.** People who already run a large model (roughly 70B-class) on their own hardware, or who use a strong hosted open model. Small models that fit on a laptop are below the floors (see Measured): they route the wrong way too often, and they state live figures without fetching them. Nothing here needs you to download a model.

**Tested adapters:** Anthropic (the official SDK), OpenAI-compatible chat completions (`openai_compat` and its presets), and Ollama native (`/api/chat`). Each one is covered by the same recorded tool-call conversations in `tests/test_providers.py`, and the OpenAI-compatible and Ollama adapters were also run live against an Ollama server.

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

In `julius.toml`, or in `julius.local.toml` to keep it to your machine. The two blocks below are **example configs**: the model names show the shape of a large setup and have not been run here (see Measured for what has). The front end on a large model you serve with Ollama:

```toml
[models.front_end]
provider = "ollama"
model = "llama3.3:70b"                   # any model your server already has
# base_url = "https://gpu-box.example:11434" # each provider has a default (here, this Mac); another machine needs https
# api_key_env = "MY_ENDPOINT_KEY"        # the NAME of the environment variable, never the key
# price_in = 0.0                         # USD per million tokens, for the cost ledger (your own hardware costs $0)
# price_out = 0.0
# timeout = 120                          # seconds
# options = { num_ctx = 16384 }          # Ollama options, or extra chat-completions fields
```

Or a hosted open model through OpenRouter (example config, not yet run; the key is read from `OPENROUTER_API_KEY`):

```toml
[models.planner]
provider = "openrouter"
model = "meta-llama/llama-3.3-70b-instruct"
price_in = 0.0       # set these to the provider's current USD per million tokens, for the cost ledger
price_out = 0.0
```

Tiers you don't list keep their default. A bad block (unknown provider, missing model, missing `base_url`, a URL that isn't http or https) stops Julius at start-up with a message naming the block. `JULIUS_TIER_<TIER>` (`provider:model`), `JULIUS_TIER_<TIER>_BASE_URL` and `JULIUS_TIER_<TIER>_OPTIONS` (JSON) override the file for one run, for example `JULIUS_TIER_FRONT_END="ollama:<your model>" python -m julius`. With no Anthropic tier left, no Anthropic key is needed: a full turn with the key unset was run on 2026-11-24.

### Endpoints: plain http only on this Mac

A `base_url` may use plain `http://` only for this Mac itself (`localhost`, `127.0.0.1`, `::1`). Anything else, a `.local` name or a LAN address included, must be `https://`: a model call carries the whole prompt, your memory notes, the transcript and tool results, plus any key. Julius refuses to start with a remote `http://` endpoint unless that tier's block says, explicitly:

```toml
allow_insecure = true   # you accept that this tier's traffic is unencrypted on your network
```

and then it prints a warning at every start naming the tier and the endpoint.

**Ollama has no authentication.** Anyone who can reach its port (11434) can run models on it and see what it is asked. Keep it bound to localhost, which is Ollama's default (`OLLAMA_HOST=127.0.0.1`). To use an Ollama server on another machine, put it behind https with authentication (a reverse proxy). Julius warns at start-up whenever a configured Ollama endpoint is neither on this Mac nor https. The coding agent's sandbox can't reach it: its only network route is the egress proxy.

### Limits on models you serve yourself

A model on your own server (Ollama, or an OpenAI-compatible server on localhost or `.local`) can get stuck repeating itself. Julius caps such tiers so one bad reply can't stall a conversation:

| Tier | Output cap | Deadline |
|---|---|---|
| `front_end` | 512 tokens | 90 s (the first turn after a model loads is the slow one) |
| other tiers | the request's own | 180 s |
| hosted endpoints | the request's own | 120 s |

Set `options = { max_output_tokens = ..., deadline_s = ... }` to change them. A reply past its deadline fails that turn ("Sorry, my model didn't answer that time"); so does output the server can't parse (Ollama's HTTP 500 for a malformed tool call), which never counts as the server being down. The front end goes to the offline model only after `[offline] failures_before_offline` failures in a row (default 2).

Two more guards apply to every non-Anthropic front end. A closing instruction tells the model its system text is private, and any reply sentence that repeats eight or more words of the system prompt is dropped (`julius/brain/leak.py`): a small model asked its name once read out its persona paragraph.

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
python evals/run_all.py --provider ollama --model <your model> --tier front_end --workers 1
```

This runs the routing, file-agent, live-data, memory, planner and research evals with that tier replaced (`--skip planner,research` leaves out the two that measure other tiers). Every eval CSV and summary gains a `provider` column, and the run writes `evals/results/providers_<date>.csv`. Use `--workers 1` for a model on a single local server: one request at a time keeps its prompt cache warm.

A provider passes a tier only at these floors:

| Floor | Value |
|---|---|
| Routing | ≥ 95% (Anthropic: 100% on 2026-11-24, 267 cases × 3 runs) |
| File agent | 100% (no unsafe action) |
| Tool-call JSON validity | ≥ 99% |
| Memory eval | 6/6 sections |
| Live data | 100%: every figure comes from a tool call made in that turn |
| Planner, research (when those tiers are replaced) | 100% |

A suite with no score, or one that didn't run, fails: nothing passes by omission.

### Measured

| Date | Tier | Provider and model | Routing | Tool JSON | File | Live data | Memory | Verdict |
|---|---|---|---|---|---|---|---|---|
| 2026-11-24 | front_end | anthropic `claude-haiku-5-5` | 100% (267 cases, ×3) | n/a (parsed by the API) | 100% | not run | free sections 100% | passes the floors measured |
| 2026-11-24 | front_end | ollama `qwen3:4b-instruct-2507-q4_K_M` (M4, 16 GB) | 87.9% (189 of the 215 cases that ran, ×3)¹ | 100% of 350 | 100% | 64.3% | 6/6 | **below the floors** |

¹ The other 52 cases were lost to a timeout cascade that is now fixed: one stuck reply took the front end offline and every later case failed instantly. A clean re-run was stopped after an hour on 2026-11-25 because local runs of this length aren't the point of the feature.

**Small models are below the floors; use a large model (roughly 70B-class) on your own hardware or a strong hosted open model.** No model is recommended for download here, and none has yet been measured above the floors other than Anthropic's.

## Why small models fall short

Measured on qwen3 4B; expect the same kinds of misses, less often, from mid-size models.

- **Routing.** It rarely escalates to the planner (plans, comparisons, briefings) and picks near-miss tools for art, saving and sample searches.
- **Live data.** Ollama can't force a tool call, and a small model often states a score, price or time without fetching it, which breaks Julius's rule that live facts come from a tool call made in that turn.
- **Repetition.** On one routing case it looped in its tool-call arguments on about one call in three, until it filled its output budget. The limits above stop this costing more than one turn.
- **Long prompts.** Julius's system prompt and tool list are about 13k tokens. Ollama's own default context is much smaller and silently cuts the prompt, so the adapter asks for 16,384 (`DEFAULT_CONTEXT` in `julius/brain/providers/ollama.py`); raise it with `options = { num_ctx = ... }` if you add tools.

## Coding tier: plan (not built)

`claude -p` stays the coding agent for now. A non-Anthropic coding model needs Julius's own edit/test loop, so the safety rules don't depend on one vendor's CLI. The plan, keeping every existing layer in `julius/agents/code_agent.py`:

1. **Same sandbox, new driver.** The task copy, the fresh git repo with no remote, the seatbelt profile, the scrubbed environment and the merge-back step stay as they are. Only the process inside the sandbox changes: a small Julius runner (`python -m julius.agents.code_loop`) instead of `claude --bare`.
2. **Tools inside the runner.** `read_file`, `list_files`, `search`, `edit_file` (exact replace, like Claude Code's Edit), `write_file` and `run` (tests and builds only). Every path is resolved inside the task folder. `run` refuses the same deny-list as today (git push, remotes, curl, ssh, rm, package installs) and has a timeout.
3. **Network.** The egress proxy's allowlist becomes per task: the tier's endpoint (for example `localhost:11434` for Ollama) instead of `api.anthropic.com`. Nothing else is reachable.
4. **Loop limits.** Step cap, wall-clock timeout and the per-task dollar cap (from `price_in` / `price_out`, or $0 locally), checked before every call, as `claude -p` is today.
5. **Evals.** `code_eval.py` gets `--provider` like the others, with its own floor: no sandbox escape ever (the existing escape cases), and a pass rate set from the first measured run.

Estimate: about 1.5 weeks (the loop and tools 3 days, sandbox and per-task proxy 2 days, the evals and escape tests 2 days, docs 1 day). That is over the one-week line, so the provider work for the other tiers ships first and this waits for its own slot. Small local models are also weak at multi-file edits today, so the measured value of this tier would be low for now.
