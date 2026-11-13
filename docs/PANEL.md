# Side panel (backend)

The panel is a client-side feature: cards are data on the read-only event feed (`panel.research`, `panel.files`, `panel.chart`, `panel.table`, `panel.images`, `panel.vision`, `panel.update`, `panel.clear`; see EVENT_FEED.md), and a client such as the orb app draws them. This page is the security model and how the pieces connect.

## What produces a card
When the research agent finishes (`research_task`), the result is handed to the panel (`julius/ui_bridge/panel_emit.py`) after it is built and before the spoken answer is returned, so the card never delays speech and a failure in the panel never changes what Julius says. The card carries the question, summary, numbered findings with citation chips, and the sources. Images from the pages that backed a finding are fetched afterwards in the background (the same SSRF-checked, GET-only fetcher as research), decoded and re-encoded with Pillow, stored, and arrive later as a `panel.update`.

**Research consistency.** The spoken summary is built only from the findings that survived verification. If any finding was dropped (its page was never opened) or the model's summary says something the kept claims do not (a number, or most of a sentence's content words), Julius speaks the kept claims instead. The card's `summary_cite` lists the sources those findings cite, with the same 1-based numbers as the card's source list and the planner's "sources: 1 Title; 2 Title" note.

**File cards.** Every reply of the file agent goes through one hook (`file_common.set_panel_sink`, set at boot). A pending plan (move, copy, rename, create, trash, empty trash) becomes a `panel.files` card with its first 25 rows (name, size, folder); yes, cancel and undo then change the card's `status` with a `panel.update`, and a newer plan expires the one still waiting. Folder listings and find results become the same kind of card with `kind: listing`. The card is only a view: nothing is confirmed from the panel, and nothing in it is a path Julius will act on (names and folders are cleaned text). `show more` reveals the next 25 rows of the newest file card; for a folder listing the agent itself cut short, it lists again with a larger limit (up to 200). When the newest card is a research card, "show more" is not handled here.

**Images.** Pictures are skipped before download when the file name or alt text says logo, icon, favicon, sprite, avatar, emoji, badge, button, pixel, spacer or placeholder, and after download when either side is under 200 px or the shape is wider or taller than 3:1. Up to 16 candidates are tried so skipped ones do not use up the eight places.

**Charts and tables.** The model calls `render_chart` with DATA (kind `bar`, `hbar`, `line`, `pie` or `table`, a title, labels and series, or columns and rows), never code. `julius/ui_bridge/charts.py` validates and bounds everything (30 labels, 5 series, 12 pie slices, 8 table columns, 50 rows, 80 characters a cell, finite numbers only), draws with matplotlib's Agg backend in the orb's colours on a transparent background, re-encodes the PNG through the same Pillow pipeline as every panel image, and publishes a `panel.chart` card (picture plus the data behind it). A table is sent as data and laid out by the UI. A bad spec comes back to the model as a spoken reason; matplotlib missing or a drawing failure never raises into the turn. The tool is only offered to the model when the request mentions a chart, graph, plot, table, visualisation or pie chart.

**Image galleries.** The research card keeps its small picture strip. "Show the images" (also "show me the pictures from the research on ...") opens the newest research card's pictures as a `panel.images` gallery card of their own: larger, pinnable, and each picture links back to its page.

## Images: the file server
`127.0.0.1:8785` (`[ui] panel_port`), GET only, `/<token>/p/<id>.<png|jpg|webp|gif>`: a random per-run token in the path, opaque ids, one private directory (`~/.julius/panel`, mode 0700), no listing, no symlinks, Host and Origin checks, `nosniff`, `Content-Security-Policy: default-src 'none'`, `Cache-Control: no-store`. Remote images are never passed through: each is re-encoded (metadata and anything appended is dropped; at most 5 MB in, 8000 px a side, 1280 px out, SVG refused). The cache is bounded by `panel_max_mb` (LRU) and 24 hours.

## What the panel can make Julius do
Only typed text, through the write channel and the usual typed-text rules (UI_CHANNEL.md): `open source N`, `save this card`, `close the panel`, `show more`, `show the images`. The URL for "open source" is read from the stored card (already limited to http/https without credentials), never from the words. Panel content is never fed back to a model as instructions.

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

**Looking at things (vision).** `julius/agents/vision.py`. Only an explicit request triggers it ("what am I holding", "look at me", "what's on my screen", "the whole screen", "look at this image"), matched by patterns, never by the model. One picture is taken: a camera frame (the one-shot helper `visiond/visiond.m` opens the camera, grabs a frame and exits, so the macOS camera indicator is on only for that moment), the frontmost window that is not Julius's own, the whole main display, or an image dropped on the orb. Locked screens and active secure password fields are never captured. The picture is shrunk to 1568 px, sent to Claude vision (Haiku; Sonnet for "look closely" and "read this") with the question, priced from real usage and recorded in the ledger, and published as a `panel.vision` card. The picture lives in an in-memory store (`PanelFiles.put_memory`: not on disk, at most 6, gone after an hour or when the service stops), is never put in conversation history or memory, and is written to disk only by "save this card".

*Permissions (macOS asks once, the first time).* **Screen Recording** is needed for "what's on my screen": System Settings, Privacy & Security, Screen & System Audio Recording, switch on the Python the service runs (for example `python3.12`), then restart the service (`python -m julius.service.service restart`); until then macOS returns an empty capture and Julius says it may need that permission. **Camera** is needed for "what am I holding": allow the prompt for `julius-visiond` on the first camera look (System Settings, Privacy & Security, Camera). If you declined it, switch it on there. Nothing else in Julius uses either permission, and the camera indicator is on only while the one frame is taken. Live check (2026-11-12): `python scripts/vision_live.py` runs a screen look, a dropped image and a camera look against the running service.

