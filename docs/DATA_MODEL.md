# Data Model

The concrete contracts your code reads and writes. Keep these stable — the whole system
(index, policy, analyzer) assumes these shapes.

## Vault layout

Folder names are a convention (configurable to match your existing Obsidian vault). The bot
writes into:

```
<VAULT_PATH>/
├── 00-Inbox/            raw captures, unprocessed
├── notes/              atomic notes (one idea per file)
├── bookmarks/          saved links
├── journal/            one file per day: YYYY-MM-DD.md
├── State.md            current state flags (energy / cycle / exam)
└── Tasks.md            task list
```

## Note types & frontmatter

Every note is Markdown with a YAML frontmatter block. The `type` field tells the analyzer
and index what they're looking at.

### Atomic note (`notes/`)
```yaml
---
type: note
tags: [ai, productivity]
created: 2026-09-18T14:05:00
related: ["[[good questions]]"]
---
Body — one idea, in the user's own words.
```

### Bookmark (`bookmarks/`)
```yaml
---
type: bookmark
url: https://example.com/product
source: instagram          # optional, where it came from
tags: [gadget, wishlist]
summary: One-paragraph LLM summary of the page.
created: 2026-09-18T14:05:00
reminder: 2026-10-02        # optional; ISO date
---
Original note the user sent, kept as context.
```

### Journal entry (`journal/YYYY-MM-DD.md`)
Metrics are **written by `JournalAnalyzer`** and are editable by hand. The schema is
**fixed** — do not add or rename metric fields at runtime.
```yaml
---
type: journal
date: 2026-09-18
mood: 4                # 1-5
energy: 3              # 1-5
productivity: 4        # 1-5
stress: 3             # 1-5
cycle_phase: follicular   # optional
exam_week: false
people: ["[[Ali]]"]
events: swing class
---
Free text: what happened, feelings, events, people.
```

### State flags (`State.md`)
The most sensitive data (energy/cycle/exam). Kept in plain Markdown, **under user control**,
so it can be read and edited by hand. `Policy` reads this note.
```yaml
---
type: state
energy: normal            # low | normal | high
cycle_phase: follicular    # optional
exam_week: false
updated: 2026-09-18
---
```

## `state_db` (SQLite) schema

Operational only — never user content. Suggested tables:

```sql
CREATE TABLE reminders (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  due  TEXT NOT NULL,               -- ISO datetime
  status TEXT NOT NULL DEFAULT 'pending',  -- pending|deferred|sent|closed
  note_path TEXT,
  created TEXT NOT NULL
);

CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  chat_id TEXT NOT NULL,
  role TEXT NOT NULL,               -- user|assistant
  text TEXT NOT NULL,
  ts TEXT NOT NULL
);

CREATE TABLE jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,               -- reminder|journal_check|morning_brief|analyze
  schedule TEXT,
  last_run TEXT
);

CREATE TABLE meta (
  key TEXT PRIMARY KEY,             -- e.g. last_nudge_ts
  value TEXT
);
```

## Index

`chromadb` collection of note chunks. Each vector carries metadata used to filter and to
cite the source:

```
metadata = { "path": "notes/....md", "type": "note", "tags": [...], "created": "..." }
```

The index is fully derived: deleting `.index/` and re-embedding the vault must reproduce it.

## Reminder lifecycle

```
pending ──(due & policy OK)──▶ sent ──▶ closed
   │
   └──(policy says not now)──▶ deferred ──▶ pending (next window)
```
