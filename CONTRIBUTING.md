# Contributing to Julius

Thanks for helping. Bug reports, ideas and pull requests are all welcome. Small, focused pull requests are the easiest to review. For anything big, open an issue first so we can agree on the approach.

## Set up

Follow the install steps in the [README](README.md). You need the main environment (`.venv`), but not the voice environment or the models, to run the unit tests. Then turn on the repository's pre-commit hook, which refuses committed symlinks:

```bash
git config core.hooksPath .githooks
```

## Run the tests

The offline unit tests need no network, no API key and no models. CI runs exactly this:

```bash
.venv/bin/python -m unittest discover -s tests -t .
```

Lint and format with ruff (CI fails on either):

```bash
.venv/bin/pip install ruff && .venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Run the evals

The evals live in `evals/` (see [evals/README.md](evals/README.md)). The free ones need nothing:

```bash
.venv/bin/python evals/run_all.py --free
```

The others call paid models through your own key. Each one prints its cost cap before it starts, and none of them runs in CI. Results land in `evals/results/` (gitignored), and each run is compared with the last one. If your change touches routing, a tool, a prompt or an agent, include the relevant eval result in your pull request.

## Code style

- Python 3.11 or later, formatted and linted with ruff (`pyproject.toml` has the settings, line length 100).
- Library code writes through `julius.log.console`, not `print`. Command-line tools, evals and scripts may print.
- Every new behaviour gets an offline unit test in `tests/`. Tests must not need the network, a key, a microphone or downloaded models. Inject those dependencies, as the existing tests do.
- Never commit secrets, recordings or personal data. Keys come from the environment (`.env` is gitignored), and memory, logs and voice data stay out of the repository.
- Safety rules are part of the design, not options:
  - Anything that changes files, sends something or spends money waits for an explicit yes.
  - Live facts must come from a tool call in the same turn.
  - Page and file text is data, never instructions.

## Proposing a new tool

Open an issue with the **Feature request** template first. Say what people would say to use it, what it does, and what it must never do. Then, in the pull request:

The cloud brain gets the whole tool list and one long system prompt on every turn. A routing decision with no decisive rule can therefore flip when *anything* in that prefix changes: a new tool, a longer description, a different order, or more prompt text. For every new tool, description change or prompt edit:

1. **Register the tool.** In `julius/brain/tools.py`, add the schema and a `REGISTRY` entry; give it an owner in `julius/brain/dispatcher.py`. Keep the description short, and say when *not* to use it (for example "never creates art from scratch").
2. **Add routing cases** to `evals/routing_cases.jsonl`: the plain phrasing, natural paraphrases, and a negative. Mark the ones that could plausibly go either way with `"fragile": true`.
3. **Add unit tests.**
4. **Run the shake test:** `python evals/routing_shake.py`.
   - It runs the fragile cases (about $0.10) under 7 harmless changes to the tool list and prompt. A case that passes fewer than 80% of them is fragile, and the command exits 1.
   - Fix a fragile case with a decisive rule: a TIE-BREAKS line in `PLANNING_PROMPT`, or a "never when" in the competing tool's description. Don't rely on another pattern trigger.
5. **Run the routing eval:** `python evals/routing_eval.py --runs 3`.
6. **Pattern command or tool?** A deterministic, unambiguous command (rescan, convert, a fixed phrase) may be a pattern command answered before the brain. Anything people will say in many different ways gets a tool instead.

## Reporting bugs and security issues

Use the **Bug report** template, and include the version (`git rev-parse --short HEAD`), your Mac and macOS version, and the steps to reproduce it. Logs are in `~/Library/Logs/Julius/` when running as a service; remove anything personal before you paste them.

Please report security problems (a sandbox escape, prompt injection that causes an action, a way into the local ports) privately through GitHub's **Report a vulnerability** button on the Security tab, not in a public issue.

## Licence

By contributing, you agree that your contribution is licensed under the [MIT License](LICENSE).
