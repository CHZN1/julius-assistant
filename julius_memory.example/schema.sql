-- Empty schema of julius_memory/memory.sqlite3 (created automatically on first run; no data ships with the repo).
CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, kind TEXT DEFAULT 'fact', source TEXT DEFAULT 'said', created REAL, used REAL DEFAULT 0, uses INTEGER DEFAULT 0, utterance TEXT DEFAULT '');
CREATE VIRTUAL TABLE mem_fts USING fts5(text);
