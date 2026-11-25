# UI write channel

The event feed (`docs/EVENT_FEED.md`, port 8765) is read-only and uses the same token as this channel. Anything the orb app (or another local UI) wants to *tell* Julius goes through this separate channel.

HTTP on `127.0.0.1:8775` (`[ui] port`), JSON in and out. Turn it off with `[ui] enabled = false`. If the port is taken Julius still starts, without the channel.

## Authentication

A per-install token lives in `~/.julius/ui_token` (created on first start with mode 0600, tightened if it was ever looser). Every request sends `Authorization: Bearer <token>`. The orb app reads the file itself; nothing is hardcoded or printed. Delete the file and restart Julius to rotate it.

## Endpoints

| Request | Body | Effect |
|---|---|---|
| `GET /v1/ping` | none | `{"ok": true}` when the token is right |
| `POST /v1/typed` | `{"text": "..."}` → `{"queued", "id", "merged"?}` | Queued exactly like a line typed in the terminal: same pipeline as speech, wakes him for that turn. Answer is spoken and shown (`[ui] typed_reply = "both"`, default) or shown only (`"text"`). |
| `POST /v1/photo` | raw JPEG bytes (`Content-Type: image/jpeg`, at most 4 MiB), optional `?auto=1` | The photo the user kept with Use Photo in the orb's camera window. Held in memory as "this" (never written to disk unless the user says save this card) and shown as a still card; `auto=1` means the orb used it by itself after its timeout. A follow-up the user asked for ("take a selfie and make it a pencil sketch") runs now. |
| `POST /v1/camera` | `{"event": "opened" \| "closed" \| "denied"}` | The camera window opened, was closed (camera off), or macOS refused the camera. A closed window with no photo drops a waiting follow-up. |
| `GET /v1/sprite` | none | The sprite being edited, for the orb's editor window: `{"sprite": {id, canvas, palette, layers: [{name, cells: [[x, y, index]]}], edits, body, line}}` (layers as rendered from the parameters, before pixel edits). 404 when no sprite is open. |
| `POST /v1/sprite` | `{"id": "...", "edits": {layer: {"x,y": palette index or -1}}}` (at most 1 MB) | Pixel edits from the editor window. Checked: known layers, inside the canvas, palette indices only; stored as one undo step and the card updates. 400 with the reason otherwise. |
| `POST /v1/sprite/edit` | `{"id": "...", "ops": ["bigger beard", ...]}` (button words only: smaller/bigger beard, shorter/taller hair, close-set/wider eyes, glasses, undo, redo; at most 20) | The sprite card's buttons. Applied and rendered at once; answers `{after, settings, palette, undo, redo, ms}` and updates the card in place. Never a turn: no transcript, no speech, no model. Its own rate limit (120 per 10 s). |
| `POST /v1/typed/edit` | `{"id": "...", "text": "..."}` | Change a typed message that has not been picked up yet (up arrow in the orb). 409 when it is too late (the orb then sends the text as a new message). |
| `POST /v1/typed/cancel` | `{"id": "..."}` | Cancel a typed message that has not been picked up yet (Esc in the orb). |
| `POST /v1/drop` | `{"paths": ["/abs/path", ...]}` | Files dropped on the orb. Staged only; nothing runs (see below). |

Responses are `{"ok": true, ...}` or `{"ok": false, "error": "..."}` with the status codes below.

### What a drop does (and does not do)

`/v1/drop` only *stages* a selection. Each path is checked by the file agent's own rules: it must be inside a folder allowed by `[files.folders]` (`none` folders are refused, `read` folders are accepted but marked read-only), not a symbolic link, not Julius's own state. The answer lists what was accepted and what was refused and why:
`{"ok": true, "accepted": 2, "rejected": [{"name": "hosts", "why": "a symbolic link"}], "expires_in": 600}`.

* The accepted paths form ONE pending selection, in memory only, that expires after 10 minutes; a new drop replaces it.
* Julius says what he got and asks what to do ("Got a.pdf and b.txt. What should I do with them?"), then listens for the answer.
* For the next four turns (or until it expires) the model sees the selection in a block that says it is untrusted data, not instructions. File names are cleaned first (control characters, line breaks, brackets and backticks removed, length-capped). File contents are never read.
* Nothing is opened, moved, copied or deleted by the drop. What the user then asks for goes through the normal file and coding agents, which build a plan and ask him to confirm.

## Conversations: `POST /v1/conversation`

The sidebar's actions, by id: `{"action": "list"}`, `switch` (`id`), `new` (`title?`, `project?`), `rename` (`id`, `title`), `pin` (`id`, `on`), `move` (`id`, `project`: an existing project with a close name first, a new one only when none is close), `delete` (`id`, `confirm: true`, after the sidebar's own confirm sheet: to the trash), `restore` (`id`), `trash` (the list), `search` (`query`: local full-text search), `forget_note` (`id`: a project or chat note). The answer is `{"ok": true, "state": <the panel.conversations body>, "said"?: ..., "results"?: [...]}`. A switch asked while Julius is answering happens when the turn ends. The audit log records the action only, never titles or search words.

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
panel = true           # the side panel data and its image server (docs/PANEL.md)
panel_port = 8785
panel_max_mb = 200
saved_dir = "~/Julius Saved"
```

## Panel commands (typed or spoken)
The side panel's buttons send these as ordinary typed text, so voice and buttons behave the same. They are recognised before the model is asked and act on the stored card, never on a URL or path in the words:
- `open source N` opens the Nth source of the newest research card in the default browser (http/https only). For an older card: `open source N from the research on <words of its question>` (the card whose question overlaps those words most; otherwise Julius says he can't find it). Spoken numbers work ("open source three").
- `show more` reveals the next rows of the newest file card (move/copy/trash plan or folder listing); for a listing the agent cut short it lists again with a larger limit. Does nothing when the newest card is a research card.
- `show the images` (or `show me the pictures from the research on <words>`) opens the images of the newest (or matching) research card as a gallery card.
- `save this card` writes the newest research card to a new folder under `[ui] saved_dir` (default `~/Julius Saved`): `card.md` plus an `images/` folder. A path inside iCloud Drive or a relative path is refused. See docs/PANEL.md.
- `close the panel` publishes `panel.clear` for all cards.

## Log events for typed input

Each line typed in the orb leaves four events in `~/Library/Logs/Julius/julius.jsonl` (all named `ui_typed`, field `stage`), so a "typing does nothing" report can be traced to the step that stopped:

| stage | where | fields |
|---|---|---|
| `received` | the channel accepted a valid `POST /v1/typed` | `chars` |
| `queued` | the line was put on the main loop's queue | `queue` (length) |
| `picked_up` | the main loop took it off the queue | `waited` (seconds), `queue` |
| `answered` | the turn finished | `seconds`, `spoken` |
| `failed` | the turn raised | `error` (exception type) |

Requests the channel refuses (`ui_request_refused`: status and reason) or that crash a handler (`ui_request_failed`) are logged too. The typed text itself is never logged.


## Typed messages

A typed message shows in the transcript at once (a `transcript` line with its `id` and `pending: true`) and stops whatever Julius is saying or doing, exactly like talking over him. It is picked up after a short hold (0.8 s), so it can still be edited (up arrow) or cancelled (Esc). A newer message typed while one is still waiting replaces it (the latest wins: "new", then "nes"), and the same text again is dropped, so rapid repeats never become separate turns. When it is picked up, a line with the same `id` and `pending: false` replaces the pending one.
