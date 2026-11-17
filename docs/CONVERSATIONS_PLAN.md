# Conversations sidebar: plan, Half 1 and Half 2 (built)

Status: **Half 1 (the backend) and Half 2 (the UI) are built**, 2026-11-16. Half 2: a collapsible sidebar in the panel (collapsed by
default; Pinned, Inbox, then each project; search; + New; per row Rename, Pin, Move to… (a close project name first, a new project only when
none is close), Delete… through a confirm sheet, to the trash; Trash with Restore), the current conversation's title in the panel header (it
opens the sidebar too), cards kept and shown per conversation (the last 20 of each), and the "Memory used" chip (each note with its scope;
Forget removes a chat or project note, and asks Julius to forget a fact about you). The same panel frame serves the attached, split and
detached layouts. Feed: `panel.conversations`, `panel.memory_used`, and `conv` on every card; channel: `POST /v1/conversation`.

Half 1 is voice and typed only:Half 1 is voice and typed only:

- `julius/conversations/store.py`: the SQLite store of section 2 (`~/.julius/conversations.sqlite3`, 0600, WAL, FTS5 over turns, titles and
  notes). Delete is the trash (restorable); nothing is purged.
- `julius/conversations/manager.py`: recording each finished turn in the current conversation; the last 8 messages of that conversation only;
  switching after the turn ends; auto-titles after his third message and summaries past 16 messages, every 12 (one Haiku call each, in the
  background, in the cost ledger); a recent conversation (12 hours) carries on after a restart. The brain gets the title and summary as an
  uncached SESSION STATE block and matching project and conversation notes (capped, about 400 tokens) with his words.
- `julius/conversations/commands.py`: the commands of section 5 (no model call), one question when two conversations match closely, delete
  asks first; and the `conversations` brain tool for looser wording (routing cases r259 to r268).
- Memory scopes: "remember for this project: ..." and "note for this chat: ..." go to the store's project and conversation scopes; global
  memory stays in `memory.sqlite3` as before (it holds 0 facts today, so nothing to migrate).
- Migration: `scripts/migrate_conversations.py` is a dry run by default (19 files, 916 turns, 81,816 bytes of text); the real run
  (`--apply --yes`) waits for Roy's yes, copies only, verifies turns and bytes per file, and logs to `~/dev/MOVE_LOG.md`.
- `[conversations] enabled = false` in julius.toml switches it off. Numbers are from the live logs (`julius_memory/costs.jsonl`, last 14 days).

## Where things are today

- **History**: one rolling history per run (`s.history`): the system prompt plus the last 8 messages (`trim_history` in `julius/runtime/turn.py`). Quitting loses it.
- **Memory**: durable facts in `julius_memory/memory.sqlite3`, recalled per turn (a small block, not the whole list); `projects.json` (projects in flight) and `last_session.json` (a summary) are pasted into the prompt as background.
- **Logs**: `julius_memory/sessions/<date>.jsonl`, one line per turn (19 files, 148 KB).
- **Cost**: Haiku turns average **$0.00021** (120 uncached input tokens, about 3,000 cache-read, 71 output); about **60 turns a day** (172 at most), about **$0.03 a day** in all. Sonnet turns are rare (36 in 14 days).

## 1. The sidebar (orb panel)

A collapsible left column inside the panel (all three layouts: attached, split, own window), 220 px, remembered open/closed.

- **Projects** as groups, then their conversations, newest first; **Pinned** on top; an **Inbox** project for anything not filed.
- **New conversation** button (also ⌘N in the panel); the current conversation is highlighted.
- **Rename**: double-click, or the ⋯ menu. New conversations get an **auto-title** after their third turn (one short Haiku call; the user's own rename always wins and is never overwritten).
- **Search** box: full-text over titles and turns (SQLite FTS5), results show the matching line.
- **Pin / unpin**, **Move to project**, **Delete**: a confirm sheet, then to a recoverable trash (30 days, "restore" in the trash view); nothing is deleted permanently without a second explicit confirmation, as everywhere in Julius.
- Cards stay per conversation: switching conversation swaps the panel's cards to that conversation's last 20 (kept in memory, as now).

## 2. Data (SQLite, `~/.julius/conversations.sqlite3`, 0600)

| table | columns |
|---|---|
| `projects` | id, name, created, pinned, archived |
| `conversations` | id, project_id, title, title_is_auto, created, updated, pinned, deleted_at |
| `turns` | id, conversation_id, t, role (user, assistant, tool), text, tool_calls (JSON), meta (JSON: tier, cost, typed/spoken) |
| `summaries` | conversation_id, up_to_turn_id, text, updated |
| `memories` | id, scope (global, project, conversation), scope_id, text, created, source_turn_id, confirmed |
| FTS5 | `turns_fts(text)`, `memories_fts(text)`, `titles_fts(title)` |

Writes happen on the turn thread (one connection, WAL mode); the panel reads through the backend, never the file.

## 3. What the brain sees (caps tokens and cost)

1. System prompt and tools: unchanged, still the cached prefix.
2. **Memory block** (section 4): at most 5 global + 5 project + 3 conversation items, about 150 to 400 tokens.
3. **Conversation summary**, only when the conversation has more than 16 messages: about 150 to 250 tokens, refreshed every 12 new messages by one Haiku call.
4. **The last 8 messages of the current conversation only** (today's rule), never another conversation's.

Switching conversation loads that conversation's last 8 messages into `s.history`; the next turn starts there. A turn in progress is never switched under (the switch waits for the turn to end).

## 4. Memory scopes and retrieval

- **Global**: about me, preferences ("I'm in Pacific time", "call me Roy"). Today's durable facts migrate here.
- **Project**: notes and files for one project ("Julius uses Haiku for routing", the project's folders). Only used inside that project's conversations.
- **Conversation**: just that chat ("we decided on 24 px for the Game Boy sprite"). Only used in that conversation.

Writing: "remember X" goes to global (as now, through staging and review); "remember for this project" goes to project; "note for this chat" goes to the conversation. Automatic captures keep today's staging and review.

Retrieval per turn (local, no model call): FTS5 BM25 between the user's words and each eligible scope, plus a small recency bonus; the narrower scope wins a tie (conversation over project over global); hard caps per scope (above); never more than 400 tokens. A **"Memory used"** chip under the answer in the panel lists each item with its scope, so it is visible why something was remembered; "forget that" on the chip removes it.

## 5. Voice and typed commands

Deterministic patterns, handled before the brain (no model call), like the panel commands:

- "new conversation", "new conversation about the sprite editor" (titled from the words)
- "switch to my Julius project" (the project's most recent conversation), "open the conversation about sprites" (search, the best match; asks when two are close)
- "what were we talking about in X": reads that conversation's summary **without** switching
- "rename this conversation to …", "pin this conversation", "move this to the Julius project"
- "delete this conversation": confirm first; to trash

A `conversations` brain tool covers looser wording ("go back to yesterday's chat about the drum loops"); it changes the tool list, so routing ×3 and the full shake are required, with TIE-BREAKS against `list_memories` / `forget_fact` / file tools.

## 6. Migration

Copy only; the originals are never moved or deleted (CLAUDE.md), and the run is logged in `~/dev/MOVE_LOG.md` with counts.

- `julius_memory/sessions/<date>.jsonl` → one conversation per file in **Inbox**, titled "<date>: <auto-title>" (19 conversations, about 150 turns); verify turn counts per file and total bytes of text.
- `memory.sqlite3` facts → global memories (counts verified).
- `projects.json` → projects; `last_session.json` → the summary of the newest conversation.
- A dry run prints what would be created; the real run needs a yes.

## 7. Plumbing

- Feed: `panel.conversations` (the list: projects, conversations, current id, pinned, trash count) sent on change and in the hello snapshot; `panel.memory_used` (the chip).
- UI channel: `POST /v1/conversation` `{action: new | switch | rename | pin | move | delete | restore, ...}`: the sidebar's buttons; every action is also a typed command, so rules and confirmations are the same as for voice.
- The sidebar is one more `registerPanelType`-style view in `shell/panel_conversations.js`; text only, never HTML.

## 8. Cost estimate

| | per turn | per day (60 turns) | per month |
|---|---|---|---|
| Today (Haiku average) | $0.00021 | $0.013 (Haiku) | about $0.40 |
| Memory block + summary in the prompt (+150 to 650 uncached input tokens at $0.10/MTok) | +$0.00002 to +$0.00007 | +$0.001 to +$0.004 | +$0.04 to +$0.12 |
| Summaries (one Haiku call per 12 messages: about 2,000 in, 200 out) | (amortised) about +$0.00003 | +$0.002 | +$0.05 |
| Auto-titles (one call per new conversation) | none | about +$0.0002 | under $0.01 |
| **Total change** | **about +15 to +45 %** of a Haiku turn | **+$0.003 to +$0.006** | **+$0.10 to +$0.18** |

Retrieval and search are local (SQLite), so free. Shorter histories per conversation can lower cost when a run would otherwise carry unrelated turns. The system prompt and tools stay the cached prefix, so cache reads are unchanged.

## 9. Effort per part

| Part | Effort |
|---|---|
| Store (schema, writes per turn, FTS5, WAL) + unit tests | 1 day |
| History per conversation, switching, summaries, auto-titles | 1 to 1.5 days |
| Memory scopes, retrieval, "Memory used" chip | 1.5 days |
| Voice/typed commands + `conversations` tool, routing ×3 + full shake | 1 day |
| Sidebar UI (orb), panel-per-conversation cards, UI tests | 2 days |
| Migration (dry run, copy, verify, log) | 0.5 day |
| Docs, retrieval eval (labelled scope questions), cost check | 0.5 day |
| **Total** | **about 7.5 to 8 days** |

## 10. Risks

- Retrieval picking the wrong scope: the chip makes it visible; a small labelled eval guards it.
- Switching mid-turn: switching waits for the turn to end.
- Prompt cache: the memory block and summary sit after the cached prefix, so the prefix stays cached.
- Deletion: trash first, a second confirmation for permanent removal, never automatic.
