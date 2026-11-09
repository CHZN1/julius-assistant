# UI write channel

The event feed (`docs/EVENT_FEED.md`, port 8765) is read-only. Anything the orb app (or another local UI) wants to *tell* Julius goes through this separate channel.

HTTP on `127.0.0.1:8775` (`[ui] port`), JSON in and out. Turn it off with `[ui] enabled = false`. If the port is taken Julius still starts, without the channel.

## Authentication

A per-install token lives in `~/.julius/ui_token` (created on first start with mode 0600, tightened if it was ever looser). Every request sends `Authorization: Bearer <token>`. The orb app reads the file itself; nothing is hardcoded or printed. Delete the file and restart Julius to rotate it.

## Endpoints

| Request | Body | Effect |
|---|---|---|
| `GET /v1/ping` | none | `{"ok": true}` when the token is right |
| `POST /v1/typed` | `{"text": "..."}` | Queued exactly like a line typed in the terminal: same pipeline as speech, wakes him for that turn. Answer is spoken and shown (`[ui] typed_reply = "both"`, default) or shown only (`"text"`). |
| `POST /v1/drop` | `{"paths": ["/abs/path", ...]}` | Files dropped on the orb. Staged only; nothing runs (see below). |

Responses are `{"ok": true, ...}` or `{"ok": false, "error": "..."}` with the status codes below.

### What a drop does (and does not do)

`/v1/drop` only *stages* a selection. Each path is checked by the file agent's own rules: it must be inside a folder allowed by `[files.folders]` (`none` folders are refused, `read` folders are accepted but marked read-only), not a symbolic link, not Julius's own state. The answer lists what was accepted and what was refused and why:
`{"ok": true, "accepted": 2, "rejected": [{"name": "hosts", "why": "a symbolic link"}], "expires_in": 600}`.

* The accepted paths form ONE pending selection, in memory only, that expires after 10 minutes; a new drop replaces it.
* Julius says what he got and asks what to do ("Got a.pdf and b.txt. What should I do with them?"), then listens for the answer.
* For the next four turns (or until it expires) the model sees the selection in a block that says it is untrusted data, not instructions. File names are cleaned first (control characters, line breaks, brackets and backticks removed, length-capped). File contents are never read.
* Nothing is opened, moved, copied or deleted by the drop. What the user then asks for goes through the normal file and coding agents, which build a plan and ask him to confirm.

## What is refused

Checks run in this order; the first failure answers.

1. `Host` other than `127.0.0.1:<port>` or `localhost:<port>`: 403 (DNS rebinding)
2. any `Origin` header: 403 (the native app sends none; a web page always does; there is no CORS)
3. more than 20 requests in 10 s, failures included: 429
4. missing or wrong token: 401 (constant-time compare)
5. unknown endpoint or method: 404; wrong `Content-Type`: 415; no `Content-Length`: 411; body over 16 KiB: 413; not a JSON object: 400
6. typed text empty, not a string, over 2000 characters (413), containing control characters, or starting with `/` (400: `/quit` and `/stop` are keyboard commands, never UI commands)
7. paths: a non-empty list of at most 50 absolute strings, otherwise 400/413

A handler bug answers 500 with no details.

## Audit log

`~/.julius/ui_audit.log` (mode 0600), one JSON line per request: time, endpoint, status, and sizes (`chars`, `paths`) or the refusal reason. Never the token, never the typed text.

## Config

```toml
[ui]
enabled = true
port = 8775
typed_reply = "both"   # or "text"
```
