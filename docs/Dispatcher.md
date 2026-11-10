# Julius dispatcher — October 1, 2026

The candidate now routes utility execution through `julius_dispatcher.py`.
This is an in-process specialist service, not additional language models.
Julius is still a development candidate, not ready for deployment.

## Ownership and execution

Time, weather, sports, markets, calculator, research, news and files each have
explicit tool ownership. Unknown tools, extra arguments, missing fields, wrong
types, invalid dates/leagues and invalid ticker syntax are rejected before work.
The candidate starts a fresh per-turn budget: at most eight utility calls.
One operation runs at a time, without automatic retries or background workers.
This avoids adding model residency or overlapping file changes.

Results carry a task ID, owner, tool, status, result code, elapsed seconds,
start time, message and structured data where available. History retains only
64 metadata entries, without file contents or queries. Validation output records
per-turn metadata. Existing utility adapters mostly still return strings;
the dispatcher recognizes their existing unavailable-result messages. A future
provider migration should make every adapter return explicit structured errors.

Human file commands pass through the existing FileSession. Preview tasks are
marked needs_confirmation. Only a later valid human confirmation can execute.
Model tools expose file reads/search/listing; mutations are absent and rejected.
An unrelated topic invalidates pending generic confirmation; a file request
clears prior weather/time follow-up context. Undo and pending-plan cancellation
remain provided by the file agent.

Direct requests use actual utility results. A failed model-selected utility now
returns its failure immediately instead of asking the model to rewrite it.
Instructions treat provider responses and file contents as untrusted data.
This is useful defense, not proof that all model replies are hallucination-free.

## Utility improvements

- Added recent news search: up to three last-day search results, retaining source,
  publication date, URL and snippet. These are search results, not verified full
  article summaries. Spoken news omits URLs; console output retains them.
- Added conservative direct routing for explicit news, league-score and ticker
  requests. More ambiguous requests still use model-selected tools.
- Market quotes retain source, currency and market timestamp, and disclose
  possible delay. Change is relative to previous close, not mislabeled today.
- Sports ties are reported as ties; unsupported dates fail instead of silently
  returning today's games. Results identify ESPN and retrieval time.
- Web search now retains source URLs rather than dropping them.
- Replaced SymPy string evaluation with bounded AST numeric arithmetic. Supports
  +, -, *, /, %, **, parentheses, sqrt, abs, pi and e. Rejects Python execution,
  attributes, comprehensions, extreme exponents and nonfinite/oversized values.
  Algebra, calculus and matrices are deliberately no longer advertised. Restoring
  them requires an isolated, resource-limited symbolic math specialist.

## Verification

98 tests pass across dispatcher, file agent, routing and voice configuration.
The dispatcher suite includes actual candidate direct and model-selected paths,
provider failures, source preservation, file-preview boundaries, stale generic
confirmation, invalid arguments, concurrency rejection and calculation limits.
Existing file crash/recovery and undo tests remain included. See
`benchmarks/2026-10-01/dispatcher/tests.txt`.

Three lightweight live requests succeeded for news, AAPL quotes and MLB scores;
responses/timings are saved in `live_tools.json`. Network sandbox DNS failures
were also correctly reported as failures before testing with network access.
Live results establish provider connectivity and response handling, not an
independent audit of every headline, market price or sports score.
No language/voice model, physical microphone, camera or playback was loaded for
this verification. News uses the existing installed DDGS library's news method:
https://pypi.org/project/ddgs/

## Remaining work and recommended utilities

1. Persistent timers/reminders and a small task list: recover on restart, handle
   sleep/missed deadlines, offer clear cancel/list commands and test delivery.
2. Unit conversions and date arithmetic with explicit units/time zones.
3. PDF/document extraction and search, including unsupported/scanned-file
   explanations and citations back to page/source.
4. Calendar integration after choosing a provider, with preview/confirmation for
   changes and duplicate prevention.
5. News preferences and read-only stock watchlists using the existing tools.
   Sports can grow into team filters, schedules and standings with provider and
   coverage tests; current scoreboard covers five leagues and two relative days.

The synchronous dispatcher has transport-level network timeouts, but no overall
wall-clock deadline, interrupt of active work, background-job queue, persistent
job resume or UI progress subscription. A cancel command currently cancels a
pending file plan only. These need a separate job-management layer before long
running tasks or deployment; never claim cancellation while a write may continue.
Physical voice interaction, everyday workflow acceptance, and further robustness
work remain pending. Vision stays off; the chosen Qwen speaker is unchanged.
