# Julius event feed (the UI interface)

Julius publishes what it is doing on a **read-only WebSocket on `127.0.0.1:8765`** (`[events]` in `julius.toml`: `enabled`, `port`). Any UI can follow it; the orb app in the README demo is one (it is not part of this repository). The server is `julius_events.py`; `python -m julius.ui_bridge.events_client` prints the events.

## Connection
- Plain RFC 6455 WebSocket, text frames, JSON, server to client only. Bound to `127.0.0.1` (not configurable).
- The handshake is refused (HTTP 403) unless `Host` is `127.0.0.1`, `localhost` or `[::1]` and any `Origin` is `http(s)://` with one of those hosts. Non-WebSocket requests get 426. Native clients send no Origin; a browser page served from another site cannot connect.
- Clients cannot change anything: frames they send are ignored except ping (answered) and close. A frame over 64 kB closes the connection. At most 8 clients; a client 300 messages behind is dropped.
- If the port is taken, Julius starts without the feed (it never fails to start over the UI).

## Messages
Every message has `type` and `t` (Unix seconds, float).

| type | fields | when |
|---|---|---|
| `hello` | `state`, `level`, `transcript` (the last 20 `transcript` messages) | once, on connect |
| `state` | `state`, `prev` | every change |
| `level` | `level` 0 to 1 | about 30 times a second while Julius speaks (his voice as it leaves the speakers: RMS x 4, clipped), then one `0`; nothing while silent |
| `transcript` | `who` (`you` or `julius`), `text`, `turn`, and for a typed message `id`, `pending`, `cancelled?` | a line the user said, or each sentence Julius says as he says it. A typed message appears at once with `pending: true`; later lines with the same `id` replace it (edited, picked up) or remove it (`cancelled: true`); `hello.transcript` holds the current state of each |

States: `idle` (awake, ready), `listening` (the user is speaking), `thinking`, `speaking`, `asleep` (ordinary sleep: wakes on "Julius"), `quiet` (microphone off until the wake hotkey).

## Side panel: `panel.*` messages
Data for a side panel (research results, file previews, charts, tables and image galleries; coding-task diffs). Every panel message also has `panel_id` (unique per card), `turn` (the transcript turn, or `null`) and `seq` (increasing). Messages carry **data only, never HTML**: all text is control-character and bidi-stripped and length-capped, URLs are http(s) only, and images are references to the panel file server, never remote URLs. A message over 48 kB is trimmed (`truncated: true`) or dropped.

| `type` | Fields | When |
|---|---|---|
| `panel.research` | `question`, `summary`, `findings` `[{n, claim, cite:[source n], verified?}]` (`verified: false`: the research agent did not find the claim's quote on the page it cites; the UI marks it unverified), `sources` `[{n, title, site, date|null, url}]`, `images` `[{n, image, alt, page_url, page_title}]`, `dropped`, `stopped|null`, `truncated?` | a research answer is ready |
| `panel.files` | `title`, `kind` (`plan` or `listing`), `action|null` (`move`, `copy`, `rename`, `create`, `trash`, `purge`), `status` (`pending`, `completed`, `cancelled`, `undone`, `partial`, `expired`, `listing`), `plan_id|null`, `where` (folder, or `from → to`, with `~`), `summary`, `items` `[{n, name, dir, size|null, kind, was?}]`, `shown`, `total`, `total_bytes|null`, `skipped`, `more` | a file plan is waiting for yes (the preview), or a folder listing / find result is ready; the first 25 rows are sent |
| `panel.chart` | `title`, `kind` (`bar`, `hbar`, `line`, `pie`), `unit`, `image` (the drawn PNG, a panel file reference), `alt`, `labels` `[str]`, `series` `[{name, values:[num]}]`, `note?` | the `render_chart` tool drew a chart; the data behind it is included so a UI can show it as a table |
| `panel.vision` | `title`, `source` (`camera`, `window`, `screen`, `image`), `question`, `answer`, `image` (a panel file reference to a picture held in memory only), `alt`, `model`, `cost_usd` (actual), `est_cost_usd` | the user asked Julius to look at something ("what am I holding", "what's on my screen", "look at this image"); one picture was taken, sent to Claude vision and answered. The picture is never written to disk unless the user says save this card |
| `panel.camera_status` | `enabled` (bool), `reason` (short text), `cause` (`settings`, `permission` or empty) | the camera cannot be used (or can again). Not a card: the orb dims and explains its camera buttons from it; the latest is also in the hello snapshot as `panel.camera_status` |
| `panel.camera_open` | `autostart` (bool), `auto_use_s` (0 to 30) | the user asked Julius to take a photo (voice or text). Not a card: the orb opens its floating camera window (live mirrored preview, shutter, close) and, with `autostart`, starts the 3-second countdown by itself. `auto_use_s` > 0 means a follow-up ("and make it pixel art") is waiting: the orb uses the photo by itself that many seconds after the shot if the user has not answered Retake or Use Photo |
| `panel.camera` | `title`, `image` (a panel file reference to the photo, held in memory only), `note` | the photo the user kept with Use Photo: a still. The camera itself lives in the orb's window; the backend only ever receives this one picture (`POST /v1/photo`, see UI_CHANNEL.md) |
| `panel.art` | `title`, `kind` (`pixel`, `compare`, `filter` or `sprite`), `before`, `after`, `compare` (optional, "" when absent: a second result shown beside `after`, the pixel-art filter next to a sprite), `export`, `small` (panel file references to pictures held in memory only: a downsized copy of the original, the result as shown, the PNG that save writes, and the result at 1 pixel per art pixel), `alt`, `summary`, `settings` (up to 8 short lines), `palette` (up to 64 colours as 6 hex digits), `editing?` (a sprite being edited: show it large, the photo small), `sprite_id?` (the sprite the card's edit buttons act on), `main?`, `reveal?` (`[w, h, photo, sprite]`: both pictures as w×h grids of 6 hex digits per pixel, `------` clear; the photo grid is all clear when there is no photo. The UI plays the sand reveal from them once, on a new card) | the user asked Julius to turn a picture into pixel art. Plain image processing on this computer, no model; the original is only read. Nothing reaches disk unless the user says save this card |
| `panel.theory` | `title`, `kind` (`key`, `scale`, `progression`, `chord`), `key`, `scale_notes` (spelled note names), `chords` (1 to 16 of `{symbol, roman, label, notes, midi, root}`: spelled note names, MIDI numbers 21 to 108 for the piano, root pitch class) | the user asked for theory help (a key, scale or mode, chord or chord progression). Every name and number is computed by Julius's own theory code, never by the model; the UI draws a piano with the selected chord highlighted and a chord chart. Nothing is stored |
| `panel.code` | `title`, `project`, `summary`, `files` `[{path, status (`added`, `modified`, `deleted`)}]`, `diff` (up to 160 lines of the unified diff, without git headers), `tests?` (the coding agent's own report about tests, labelled as such) | "show the changes" after a coding task: what the agent changed in its scratch copy. Nothing is in the project until the user merges it |
| `panel.table` | `title`, `unit`, `columns` `[str]`, `rows` `[[str]]` (one cell per column), `note?` | the `render_chart` tool with kind `table`: plain text cells, laid out by the UI |
| `panel.images` | `title`, `question`, `images` `[{n, image, alt, page_url, page_title}]` | "show the images": the pictures of a research card as a gallery of their own |
| `panel.update` | `target` (a card's `panel_id`), `images?` (more images, numbered after the card's existing ones), `note?`, `items?` (more file rows, numbered after the existing ones), `shown?`, `more?`, `status?` (a file card's new status), `summary?`, `after?`, `export?`, `small?`, `settings?`, `palette?`, `editing?` (a sprite card after an edit: the new pictures and settings in place) | pieces that arrive late: fetched images, the rows revealed by "show more", a plan being completed, cancelled or undone |
| `panel.clear` | `scope` (`all` or `card`), `target|null`, `reason` (`new_topic`, `dismissed`, `expired`, `done`) | remove one card or all |
| `panel.conversations` | `current` (id or null), `title`, `project`, `projects` `[{id, name, pinned}]`, `conversations` `[{id, title, project, pinned, updated, n}]` (pinned first, then newest; empty ones left out), `trash` (count), `messages` `[{who, text}]` (the current conversation's last 12, for the transcript when he opens another one) | the conversation list changed (a turn was kept, a title or summary arrived, a sidebar or voice action). Not a card: the orb's sidebar and the panel header's title come from it; the latest is also in the hello snapshot as `conversations` |
| `panel.memory_used` | `items` `[{id, scope (global, project, conversation), label, text}]` | the notes the brain was given for this turn (empty clears the chip). Not a card: the panel's "Memory used" chip |

Every card also carries `conv`: the id of the conversation it was made in. The orb shows the current conversation's cards; the backend keeps the last 20 cards of each conversation in memory (120 in all).

`summary` and each finding's `claim` may use a tiny markdown subset, which a UI renders as text nodes (never as HTML): `**bold**`, `*italic*`, `` `code` ``, `- ` bullet lines and `1. ` numbered lines; paragraphs are separated by a blank line, and everything else is plain text. Line breaks are kept in those two fields only.

Citation numbers are 1-based and the same in `findings[].cite`, `sources[].n` and the typed command "open source 3". `image` is `/p/<id>.<png|jpg|webp|gif>`, to be fetched from `hello.panel.base` + that path (a localhost-only, token-protected file server; clients must not load any other image URL). Source links are shown as text; opening one is done by typing "open source N" to the write channel, so Julius opens it, not the UI.

`hello` gains `panel`: `{"cards": [last 20 panel cards (any `panel.*` card type), oldest first, with updates already folded in], "base": "<file server origin and token path>"}`, so a UI that reconnects restores its panel. Example `panel.research`:

```json
{"type":"panel.research","t":1.0,"panel_id":"c1-1","turn":4,"seq":1,"question":"How tall is it?","summary":"It is 830 m tall.","findings":[{"n":1,"claim":"The tower is 830 m tall.","cite":[1]}],"sources":[{"n":1,"title":"The Tower","site":"example.org","date":null,"url":"https://example.org/tower"}],"images":[],"dropped":0,"stopped":null}
```

Sending anything *to* Julius (typed text, dropped files) is a separate, authenticated local channel on `127.0.0.1:8775`, never this feed; see [UI_CHANNEL.md](UI_CHANNEL.md).

## Compatibility
New message types and new fields may be added; clients must ignore what they do not know. The state list may grow.
