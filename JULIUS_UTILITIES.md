> October 2 evening: explicit `complete reminder <name/reference>` and
> `mark reminder <name> done` now complete local/linked Apple reminders.
> Announcement does not complete them; failed remote completion preserves local state.
> Phone sync remains unverified, with full iCloud storage reported.

> October 2 update: Apple iCloud Calendar/Reminders is now connected and tested.
> Read JULIUS_APPLE_AND_LIVE_TESTS.md for current integration and runtime limits;
> the October 1 provider-pending notes below describe the earlier milestone.

# Julius utilities — October 1, 2026

Implemented and connected to the candidate runtime: persistent reminders/tasks,
dimension-checked conversions, explicit date/time calculations, bounded document
extraction/search, and a local calendar preview/confirmation foundation.
A real Apple/Google/Outlook calendar connection is pending the user's provider choice.
These are development features; tests do not prove error-free operation or
establish deployment readiness.

## Commands

Reminders and tasks are owned by explicit human commands. They are absent from
model mutation tools. For example:

- `remind me in 5 minutes to check the oven`
- `set a timer for 10 minutes called laundry`
- `remind me tomorrow at 9 am to call Dad`
- `remind me at 2026-10-05T09:00 in America/Los_Angeles to call Dad`
- `show my reminders` / `show my tasks`
- `add task buy milk` / `complete task buy milk`
- `cancel reminder check the oven`

Exact names work for completion/cancellation. If multiple active items have the
same name, Julius asks for the exact reference from the list. Vague durations,
missing AM/PM and ambiguous daylight-saving times trigger clarification.
Reminder confirmations display a local clock time and timezone abbreviation.

The model can read local planner items with `list_planner_items`, convert units
with `convert_units`, and calculate dates with `date_calculate`. Examples:

- `convert 1 mi to km`
- `32 fahrenheit to celsius`
- `date_calculate(start="2026-03-07T12:00", operation="add_days", amount=1)`

Supported dimensions: length, mass, time, volume, temperature. `us_gal` and
`us_cup` explicitly use US measures; ambiguous gallons/cups and currency conversion
are rejected. Exact decimal constants are used with a concise numeric display.
Date operations accept ISO date/time and IANA zones. Calendar days preserve local
clock time; elapsed hours and differences use UTC. Nonexistent or repeated local
clock times require clarification or explicit UTC offsets.

Document commands:

- `read document "Documents/report.pdf"`
- `search document "Documents/report.pdf" for "deadline"`
- Normal file-controller `read "Documents/report.pdf"` also uses the extractor.

PDF pages, DOCX paragraphs and UTF-8 text lines carry source references. Search
is literal and case-insensitive, returning bounded matching excerpts. It searches
only the extracted portion, not a semantic index. DOCX extraction covers body
text, including table paragraphs, but not headers, footers, comments or visual
layout. Legacy `.doc`, image OCR and spreadsheet formula interpretation are not
supported. A missing PDF text layer is reported rather than invented.

## Persistence and delivery

`julius_memory/planner.sqlite3` stores planner items, with private file mode,
transactions, an indexed due queue and a version marker. SQLite serializes
concurrent writers. Failed transactions roll back. Exact duplicate active tasks
and events are not added twice; reminders with identical titles and due times
are also deduplicated. Up to 1000 active items and 16 MiB history are allowed.
Lists show up to 100 entries. Large spoken lists are shortened; full results
remain on screen. No user items were created during validation.

The runtime checks due reminders once per second while it is idle and not
recording. Reminders are claimed with a 120-second delivery lease, acknowledged
only after speech returns, and retried after a playback failure. Expired claims
recover after restart. Each pass handles at most three items. Late reminders
are labeled missed. The text announcement is printed before playback. Qwen
reminder playback unloads the language backend first to reduce simultaneous
model residency.

Delivery is **at least once**: a crash after playback but before acknowledgement
can repeat an announcement. Julius must be running; this is not a macOS alarm or
a background service. While Julius is processing or speaking, reminders wait
until the main loop resumes. OS sleep postpones delivery until wake/resume.
Physical playback, wake/sleep acceptance and background scheduling still need
verification before relying on reminders. Calendar events do not yet have
automatic reminder alarms.

State corruption, unknown versions and storage failures are reported without
silently replacing history. A planner failure does not exit the candidate.
Speech failure after a successful human write preserves the saved item and
shows the result as text. Direct utility turns now share the nine-message
conversation-history bound, preventing indefinite growth.

## Document isolation and dependencies

The main process resolves paths using the existing file-agent scope, rejects
symlinks, opens a regular file without following its final symlink, checks a
20 MiB input budget and snapshots it. The disposable parser receives bytes,
not a filename, so parser reads do not bypass the file scope.

A 12-second parent timeout stops the worker. The worker requests an eight-second
CPU limit and 256 MiB data-segment limit where the OS supports these limits.
Output is bounded to 20 PDF pages, 200 text/DOCX locations and 20,000 characters;
search returns up to 20 matching locations. Partial extraction is labeled.
Large decoded PDF content, DOCX XML entities, invalid archives, encrypted PDFs,
malformed documents and missing dependencies fail explicitly. Isolation and
limits are useful protection, not a universal sandbox or perfect layout parser.

`JULIUS_DOCUMENT_PYTHON` can select a Python interpreter with pypdf installed.
On this Mac it automatically uses the existing Codex bundled Python, which
already includes pypdf; otherwise it uses the current interpreter. The main
Julius interpreter does not need a new PDF package. Packaging an independent
Julius installation will need an explicitly provisioned parser environment.
No package was installed into the torch or voice environments.

## Local calendar foundation

Example:

`schedule event "Dentist" from 2026-10-05T09:00 to 2026-10-05T10:00 in America/Los_Angeles`

This previews a local Julius event and states that it is saved only in Julius.
A later `yes` confirms; `cancel` discards. The draft expires in 15 minutes and an
unrelated turn clears generic confirmation. Events require a future start and
an end after it, within 31 days. Duplicate events are prevented and overlaps
are reported after creation. `show my calendar` and `cancel event Dentist` work
against the local store. This does not claim an Apple/Google/Outlook connection.

Next required calendar step: choose a provider, implement its authenticated
adapter, then test read/preview/write/cancel, duplicate prevention, timezone
conversion, permission denial and provider outages against that provider.
No external account was connected and no external event was written.

## Verification

128 tests pass across utilities, dispatcher, files, routing and voice
configuration. Tests use temporary state and include concurrent duplicates,
transaction rollback, corrupt storage, cancellation, restart/lease recovery,
failed playback, DST gaps/folds, leap dates, dimension errors, encrypted/blank/
malformed PDFs, actual PDF text extraction, DOCX entities, truncation and worker
timeouts. Actual candidate process functions also handle task/reminder writes,
conversions and document reads without a model, preserve writes after speech
failure, and maintain bounded history. Syntax compilation passed.

Evidence: `benchmarks/2026-10-01/utilities/tests.txt` and `performance.json`.
The lightweight benchmark used temporary data and no inference or devices:
median idle reminder check 0.109 ms (500 samples), conversion 0.00079 ms (500),
task creation 0.345 ms (20); one isolated text extraction 24.94 ms. Parent peak
RSS was about 19.8 MiB, excluding child parser and all Julius models. These
measurements show low overhead for these fixtures; they do not establish a
global performance optimum or worst-case document memory use.

Relevant implementation references:
https://docs.python.org/3/library/zoneinfo.html
https://docs.python.org/3/library/sqlite3.html
https://pypdf.readthedocs.io/en/stable/user/extract-text.html
