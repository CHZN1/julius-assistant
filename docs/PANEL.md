# Side panel (backend)

The panel is a client-side feature: cards are data on the read-only event feed (`panel.research`, `panel.update`, `panel.clear`; see EVENT_FEED.md), and a client such as the orb app draws them. This page is the security model and how the pieces connect.

## What produces a card
When the research agent finishes (`research_task`), the result is handed to the panel (`julius/ui_bridge/panel_emit.py`) after it is built and before the spoken answer is returned, so the card never delays speech and a failure in the panel never changes what Julius says. The card carries the question, summary, numbered findings with citation chips, and the sources. Images from the pages that backed a finding are fetched afterwards in the background (the same SSRF-checked, GET-only fetcher as research), decoded and re-encoded with Pillow, stored, and arrive later as a `panel.update`.

## Images: the file server
`127.0.0.1:8785` (`[ui] panel_port`), GET only, `/<token>/p/<id>.<png|jpg|webp|gif>`: a random per-run token in the path, opaque ids, one private directory (`~/.julius/panel`, mode 0700), no listing, no symlinks, Host and Origin checks, `nosniff`, `Content-Security-Policy: default-src 'none'`, `Cache-Control: no-store`. Remote images are never passed through: each is re-encoded (metadata and anything appended is dropped; at most 5 MB in, 8000 px a side, 1280 px out, SVG refused). The cache is bounded by `panel_max_mb` (LRU) and 24 hours.

## What the panel can make Julius do
Only typed text, through the write channel and the usual typed-text rules (UI_CHANNEL.md): `open source N`, `save this card`, `close the panel`. The URL for "open source" is read from the stored card (already limited to http/https without credentials), never from the words. Panel content is never fed back to a model as instructions.

## Saved cards
`save this card` creates a folder `YYYY-MM-DD HHMM <question-slug>` under `[ui] saved_dir` (default `~/Julius Saved`, local on purpose; a path inside iCloud Drive is refused) with `card.md` (question, summary, findings with `[n]` citations, sources as links, image captions) and `images/`. Names are letters, digits and dashes only, and an earlier save is never overwritten.

## Config
```toml
[ui]
panel = true
panel_port = 8785
panel_max_mb = 200
saved_dir = "~/Julius Saved"
```
If the port is taken or the directory is not writable, Julius starts without the panel and says so in the console.
