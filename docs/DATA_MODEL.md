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
If the page could not be fetched, the bookmark is still saved with empty `summary`/`tags`
and the body ends with a `> Sayfa alınamadı: <reason>` line.

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
**14:05** — Free text in the user's own words: what happened, feelings, events, people.

**21:40** — Each entry is appended verbatim by JournalWriter with its time.
```

`JournalWriter` creates the file with only `type` and `date`. `JournalAnalyzer` fills
`mood`/`energy`/`productivity`/`stress` (integers 1-5) after the day ends, and **only the
ones still empty** — a value you set by hand is never overwritten, and a metric the text does
not support is left out rather than guessed. `people`, `events`, `cycle_phase` and
`exam_week` are not filled automatically yet. A journal
day runs from **04:00 to 04:00**, so an entry written at 01:30 belongs to the previous date.

### State flags (`State.md`)
The most sensitive data (energy/cycle/exam). Kept in plain Markdown, **under user control**,
so it can be read and edited by hand. `Policy` reads this note. Written by `StateManager`
through `NoteWriter.update_state` (other keys you add by hand are kept); never indexed, and
values never appear in logs or `state_db`.
```yaml
---
type: state
energy: low               # low | normal | high
energy_until: 2026-09-21   # optional end date; set by the tool (default +3 days)
exam_week: true
exam_until: 2026-09-25     # optional end date; set by the tool (default +7 days)
cycle_phase: follicular    # optional: menstrual | follicular | ovulatory | luteal
updated: 2026-09-18
---
```

A flag with an end date switches itself off after that date; without one (e.g. edited by
hand) it stays until changed. Effects, for assistant-initiated `nudge`s only (reminders the
user set are never held back): exam week → none; low energy → at most 1 a day; high energy and
cycle phase → no change.

## `state_db` (SQLite) schema

Operational only — never user content. Implemented in `storage/state_db.py`; the schema
version is tracked in `PRAGMA user_version`. `status` and `role` also have `CHECK` constraints,
and `reminders(status, due)` and `messages(chat_id, id)`, `messages(ts)` are indexed.

All datetimes are **naive local time** stored as ISO text with second precision
(`2026-09-18T14:05:00`), so string comparison in SQL equals time comparison.
Timezone-aware datetimes are rejected.

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
  kind TEXT NOT NULL UNIQUE,        -- reminder|journal_check|morning_brief|analyze (one per kind)
  schedule TEXT,
  last_run TEXT
);

CREATE TABLE meta (
  key TEXT PRIMARY KEY,             -- e.g. last_nudge_ts
  value TEXT
);
```

## Index

`chromadb` collection `notes` (cosine) of note chunks at `INDEX_PATH` (default `.index/`,
outside the vault). Each chunk is ~500 characters of "title, summary, tags, body"; its id is
`<path>#<n>`. Each vector carries metadata used to filter and to cite the source:

```
metadata = { "path": "notes/....md", "type": "note", "title": "...",
             "tags": "ai, productivity", "created": "...", "chunk": 0 }
```

`tags` is a comma-joined string (chromadb metadata values are scalars). Vectors come from the
local multilingual model in `embeddings.py`.

The index is fully derived: deleting `.index/` and re-embedding the vault must reproduce it —
`scripts/rebuild_index.py` does exactly that.

## Reminder lifecycle

```
pending ──(due & policy OK)──▶ sent ──▶ closed
   │
   └──(policy says not now)──▶ deferred ──▶ pending (next window)
```
