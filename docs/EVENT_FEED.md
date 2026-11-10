# Julius event feed (the UI interface)

Julius publishes what it is doing on a **read-only WebSocket on `127.0.0.1:8765`** (`[events]` in `julius.toml`: `enabled`, `port`). Any UI can follow it; the first one is the private `julius-orb` app. The server is `julius_events.py`; `python -m julius.ui_bridge.events_client` prints the events.

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

## Reserved for later (not implemented)
`panel.*` messages (images, results, code and diffs for a side panel). Sending anything *to* Julius (typed text, dropped files) is a separate, authenticated local channel on `127.0.0.1:8775`, never this feed; it is specified in its own document when it exists.

## Compatibility
New message types and new fields may be added; clients must ignore what they do not know. The state list may grow.
