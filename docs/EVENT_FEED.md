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
| `transcript` | `who` (`you` or `julius`), `text`, `turn` | a line the user said, or each sentence Julius says as he says it |

States: `idle` (awake, ready), `listening` (the user is speaking), `thinking`, `speaking`, `asleep` (ordinary sleep: wakes on "Julius"), `quiet` (microphone off until the wake hotkey).

## Side panel: `panel.*` messages
Data for a side panel (research results first; file previews, diffs, charts and tables follow in later versions). Every panel message also has `panel_id` (unique per card), `turn` (the transcript turn, or `null`) and `seq` (increasing). Messages carry **data only, never HTML**: all text is control-character and bidi-stripped and length-capped, URLs are http(s) only, and images are references to the panel file server, never remote URLs. A message over 48 kB is trimmed (`truncated: true`) or dropped.

| `type` | Fields | When |
|---|---|---|
| `panel.research` | `question`, `summary`, `findings` `[{n, claim, cite:[source n]}]`, `sources` `[{n, title, site, date|null, url}]`, `images` `[{n, image, alt, page_url, page_title}]`, `dropped`, `stopped|null`, `truncated?` | a research answer is ready |
| `panel.update` | `target` (a card's `panel_id`), `images?` (more images, numbered after the card's existing ones), `note?` | pieces that arrive late, such as fetched images |
| `panel.clear` | `scope` (`all` or `card`), `target|null`, `reason` (`new_topic`, `dismissed`, `expired`, `done`) | remove one card or all |

Citation numbers are 1-based and the same in `findings[].cite`, `sources[].n` and the typed command "open source 3". `image` is `/p/<id>.<png|jpg|webp|gif>`, to be fetched from `hello.panel.base` + that path (a localhost-only, token-protected file server; clients must not load any other image URL). Source links are shown as text; opening one is done by typing "open source N" to the write channel, so Julius opens it, not the UI.

`hello` gains `panel`: `{"cards": [last 10 panel.research messages, oldest first, with updates already folded in], "base": "<file server origin and token path>"}`, so a UI that reconnects restores its panel. Example `panel.research`:

```json
{"type":"panel.research","t":1.0,"panel_id":"c1-1","turn":4,"seq":1,"question":"How tall is it?","summary":"It is 830 m tall.","findings":[{"n":1,"claim":"The tower is 830 m tall.","cite":[1]}],"sources":[{"n":1,"title":"The Tower","site":"example.org","date":null,"url":"https://example.org/tower"}],"images":[],"dropped":0,"stopped":null}
```

Sending anything *to* Julius (typed text, dropped files) is a separate, authenticated local channel on `127.0.0.1:8775`, never this feed; see [UI_CHANNEL.md](UI_CHANNEL.md).

## Compatibility
New message types and new fields may be added; clients must ignore what they do not know. The state list may grow.
