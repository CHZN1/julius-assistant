# Julius

A local-first voice assistant for macOS: always-on wake word, streaming speech recognition (MLX Whisper), a cloud brain (Claude Haiku for conversation, Sonnet for planning) with an offline fallback, a natural MLX voice, duplex audio with echo cancellation so you can talk over it, and agents for files, memory, reminders and coding, all gated by spoken confirmation.

![The Julius orb following a conversation](docs/orb_demo.gif)

*The orb above is a separate UI (a private companion app). It follows Julius through a small read-only WebSocket, documented in [docs/EVENT_FEED.md](docs/EVENT_FEED.md); anything that speaks that protocol can drive a UI.*

## Run
- `python -m julius.service.service on` installs it as a login service; `status`, `logs`, `smoke [--restart]`, `doctor`.
- `python -m julius` runs it in the foreground; type or say "Julius".
- Config is `julius.toml`; keys come from the environment or `.env` and are never logged.

## Interfaces
- **Event feed** (read-only, `ws://127.0.0.1:8765`): state, speaker level and transcript lines. See [docs/EVENT_FEED.md](docs/EVENT_FEED.md); `python -m julius.ui_bridge.events_client` prints it.
- Roadmap and measurements: [JULIUS_ROADMAP.md](JULIUS_ROADMAP.md), `JULIUS_*.md`.

## Tests
`python -m unittest discover -p "test_*.py"` (offline; evals under `evals/` use paid model calls and say so).
