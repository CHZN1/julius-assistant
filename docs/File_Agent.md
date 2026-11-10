# Julius file-agent diagnostic and improvement — October 1, 2026

The candidate launcher now uses a scoped, deterministic file service and a human-command controller. The file service performs no model inference and opens no devices. It has a structured contract suitable for a later UI and dispatcher, with an explicit `agent: files` owner, result code, short message, data, plan ID, per-operation status, and verified filesystem paths.

## Diagnosed problems

The previous service checked containment with string prefixes, so a similarly named sibling path could pass the test. Unknown source folders silently fell back to Documents, destination names could be invented, fuzzy directory matches could choose the first of several candidates, and directory discovery recursively traversed the full tree despite a nominal depth limit. Images meant only PNG. File reads loaded an entire file before truncating it, and searches/listings could hide incomplete results.

Approval matching accepted broad fragments such as “yes, but only the PDFs”. Mutating tools were exposed to the model and trusted instructions to wait for approval. Plans lacked selection fingerprints, expiry, session binding, and durable operation intent. Execution could change collision names after preview and marked an entire plan done despite partial failures. Undo did not correctly exclude already reversed batches, swallowed failures, and could overwrite a recreated original or remove an edited result. Legacy operation logs were insufficient to prove ownership after interruption.

## Implemented foundation

- `julius_file_agent.py`: structured service; default scope Documents, Desktop, Downloads; exact component and resolved-path checks; symlink exclusion; protected internal history. Allowed roots can be passed explicitly by a future permission UI.
- `julius_router.py`: bounded folder discovery, conservative fuzzy matching, literal quoted names and spoken punctuation, explicit clarification for unknown folders and unclear selections. Explicit new destinations are supported. Conditional confirmations do not execute plans.
- `julius_file_session.py`: current preview ownership, separate-turn confirmation, cancellation, re-preview, and undo. Unrelated commands clear generic confirmation eligibility. Revising the request creates a new plan rather than approving the old one.
- `julius_files.py`: compatibility facade and display formatting. Legacy plan/log files are retained, not automatically trusted, migrated, or executed.
- `julius_tools.py`: shared scoped read/list/find service. Model tool schemas and registry no longer expose file mutation or undo. A model statement cannot serve as confirmation.
- `julius_candidate.py`: actual human file commands use the controller before model routing. Short spoken results are separate from detailed on-screen results. Validation records structured file results.

Capabilities: bounded filename search, listing, UTF-8 text reading, move, copy, single-file rename, and exact-text creation. Mutations use preview and confirmation. Recursive batches preserve relative subfolders. Collision names are not silently changed. Text creation is limited to exact supplied text; generated documents belong to a future document specialist.

Preview snapshots contain metadata and streaming SHA-256 hashes. Execution checks the complete selection and destination before changing any file, then checks each operation again. Published destinations use exclusive hard links rather than an overwrite-prone rename. Cross-filesystem move/copy stages and verifies a copy before publication; the original is retained on detected failures. Same-filesystem moves use hard links followed by removal of the original path, avoiding unnecessary content copying.

History updates use atomic replacement, fsync, and cross-process/thread locking. Operation intent and destination ownership are journaled before publication. Partial failures stop the batch and report exact per-file states. Successful changes remain undoable. Recovery can recognize interrupted publication and interrupted undo; uncertain ownership is left for review.

Undo checks content, metadata, and file identity. It refuses occupied original paths and edited/replaced outputs. Undoing a copy or created text file removes only an unchanged output owned by that operation. Original source files are not deleted for copy. Already undone batches are skipped, and repeated undo selects the next older batch. Conflicted batches remain available for review/retry instead of being marked reversed.

## Resource limits and user-facing behavior

Search: at most 10,000 entries / 200 results. Folder matching: depth two / 1,000 inspected entries. Mutation preview: at most 100 files / 512 MB. UTF-8 preview: at most 20,000 characters. New text file: at most 1 MB. Plan expiry: fifteen minutes; old-session approvals cannot run. Persistent history has a 16 MB read ceiling requiring archival rather than unbounded loading.

Incomplete searches are labeled; an incomplete selection cannot become a mutation batch. Metadata changes are checked before hashing, avoiding a large read when a selected file obviously changed. Hidden entries and symlink traversal are excluded by default. Internal file-history paths are protected from the service's own operations. IDs and full paths stay in structured/on-screen details; spoken previews describe the action and ask for yes or cancel.

Example commands:

- `list files in Downloads`
- `find "notes.txt" in Documents`
- `read "notes.txt"`
- `move "notes.txt" from Documents to Archive`
- `copy images from Downloads to Documents/Photo Backup`
- `move PDFs to a new folder named Finished`
- `rename "notes.txt" to "meeting notes.txt"`
- `create "notes.txt" containing "Call the dentist tomorrow"`
- `yes`, `yes, please`, `cancel`, `show the last plan`, `undo`

Unknown or ambiguous names produce clarification, not guessed operations. UI selection can later bind exact paths instead of interpreting “those files”.

## Verification

`test_julius_file_agent.py` exercises real operations only in temporary directories. It includes scope-prefix escapes, traversal, symlinks, ambiguous names, quoted paths, images, read limits, changed files, late collisions, copy/create/rename, cancelled/expired/old-session plans, repeated undo, replaced outputs, partial failure, restart intent recovery, concurrent execution, ownership recovery, corrupted state, and unclear/conditional commands.

An integration check executes the candidate's actual `process` and `handle_files` functions through preview → confirm → undo. Speech, memory logging, and model inference are mocked; it proves file execution bypasses the conversational model, not live microphone/playback correctness. Shared model read tools are tested against the same temporary scoped service.

Final regression evidence and synthetic performance measurements are saved in `benchmarks/2026-10-01/files/`. The generated benchmark uses 12,000 search files plus fifty small files for move/undo. Results are local single-run measurements, not performance guarantees for large files or network/cloud folders.

## Limits and next dispatcher work

This is an improved base, not a deployment-readiness claim. PDF/Word/spreadsheet/media content extraction is explicitly unsupported by the text reader; a document agent is still needed. Search is filename-based and bounded, not semantic content indexing. Only files, not entire directory moves, are mutated. Symbolic-link folders, live cloud-provider behavior, permission dialogs, external volumes, and arbitrary hostile concurrent writers were not validated. Julius operations are serialized and common changes are detected; other applications are not locked into a filesystem transaction with Julius.

Pending-plan cancellation is implemented. Mid-batch interactive cancellation, background task deadlines, progress events, and broad natural-language document generation belong to the dispatcher/background-worker phase. The native service currently runs synchronously. Legacy scripts remain unchanged but cannot use the old implicit model/execution confirmation path; use `run_julius.sh` / the candidate controller.

Next: build the dispatcher around this structured `files` capability boundary, with task ownership, explicit UI/human confirmation provenance, progress, timeouts, and cancellation. Keep a single shared conversational model and avoid loading a separate model for this deterministic file specialist.

Final recorded check: 83 tests passed, including 66 file-specific tests; zero failures/errors/skips. Syntax compilation passed for the candidate, tools, service, session, router, facade, and tests.

Latest synthetic run: bounded search 9.391 ms at 10,000 entries; folder inventory 2.397 ms; fifty-file preview 16.093 ms; move 174.819 ms; undo 168.15 ms. Process peak RSS approximately 19.4 MiB, with no models loaded.
