# Memory example (no real data)

Your real memory lives in `julius_memory/` (git-ignored: `memory.sqlite3`, `MEMORY.md`, session logs).

- `schema.sql`: the empty database layout; Julius creates it on first run.
- `sample_memories.json`: invented facts about a fictional person, to try the feature.
  `python julius_memstore.py init ~/julius-demo.sqlite3 --sample` makes a demo database; point `JULIUS_MEMORY_DIR` at a folder holding it as `memory.sqlite3`.

Voice: "remember that ...", "forget that", "forget <topic>", "what do you remember (about X)", "what did you just remember", "actually, ..." (corrects a fact), "undo that".
