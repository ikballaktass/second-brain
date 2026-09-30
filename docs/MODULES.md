# Module Reference

One entry per module: what it owns, its key API, what it depends on, and its current build
status. `Status: stub` means the class/signatures exist but the body raises
`NotImplementedError` — the issue backlog tracks turning these green.

> Convention: a module never imports from a layer above it (see ARCHITECTURE.md).

## interface

### `interface/telegram_gateway.py` — `TelegramGateway`
The single bidirectional channel. Receives user messages **and** sends proactive nudges.
Only `TELEGRAM_ALLOWED_CHAT_ID` is served (single-user).
- `start()` — begin long-polling, register the handler
- `send(chat_id, text)` — used by both replies and the proactive path
- `_on_message(update)` — auth-check, then hand off to the orchestrator
- Depends on: `orchestration` · Status: **stub**

## orchestration

### `orchestration/router.py` — `Router`
- `classify(message) -> Intent` — cheap/fast intent detection
- Depends on: `llm_client`, `models.Intent` · Status: **stub**

### `orchestration/orchestrator.py` — `Orchestrator`
Core decision center. Holds the tool registry and drives tool-calling.
- `handle(message) -> str` — classify → build context → call tool(s) → reply
- `_call_tool(name, args)`
- Depends on: `router`, `context_builder`, `llm_client`, `tools` · Status: **stub**

### `orchestration/reminder_dispatcher.py` — `ReminderDispatcher`
The proactive send path for reminders; the scheduler's `on_due` handler.
- `await dispatcher(due)` — `policy.decide(now, "reminder")`; allowed → one templated message
  (`⏰ Hatırlatma: …`, no LLM), each reminder `sent`, `record_nudge`; not allowed → each
  deferred to `retry_at` (fallback +30 min); send failure → deferred +5 min
- `send` is injected (interface sits above orchestration)
- Depends on: `reminder_manager`, `policy` · Status: **done**

### `orchestration/context_builder.py` — `ContextBuilder`
- `build(message) -> dict` — history + related notes (retriever) + current state; manages
  the token budget
- Depends on: `state_db`, `retriever` (Phase 4) · Status: **stub**

## tools

All tools implement `tools/base.py` → `Tool` (`name`, `run(args)`).

| Module | Class | Responsibility | Phase | Status |
|--------|-------|----------------|-------|--------|
| `note_writer.py` | `NoteWriter` | Write/update Markdown notes; **only** vault writer | 0 | stub |
| `link_capturer.py` | `LinkCapturer` | Fetch URL → summarize + tag → `NoteWriter.save_bookmark` | 1 | done |
| `reminder_manager.py` | `ReminderManager` | Reminder create/list/close + lifecycle (`due`, `mark_sent`, `defer`) on StateDB; registered only when `proactive` is on | 3 | done |
| `task_manager.py` | `TaskManager` | Tasks with priority | 3 | stub |
| `calendar.py` | `CalendarTool` | Google Calendar read-only: `list` a day's events (LLM) and `busy_until(t)` for Policy; 5-min cache; off until OAuth is set up | 2 | done |
| `retriever.py` | `Retriever` | Semantic search over the vault (`query`, `k` ≤ 10) → `path · score · snippet`; registered when `recall` is on (default) | 4 | done |
| `journal_writer.py` | `JournalWriter` | Open/append today's journal note | 5 | stub |
| `journal_analyzer.py` | `JournalAnalyzer` | End-of-day metric extraction (fixed schema) | 5 | stub |

`JournalAnalyzer` is intentionally **not** a `Tool` — it is driven by the scheduler, not by
user intent. It must fill the fixed metric schema only and never invent new metrics.

## proactive

### `proactive/scheduler.py` — `Scheduler`
APScheduler `AsyncIOScheduler` inside the bot's event loop, started/stopped by the
gateway's `on_startup` / `on_shutdown` hooks (only when `proactive` is on).
- `async tick(now)` — fetch due reminders from `state_db`, hand them to `on_due`; never raises
- `add_job(kind, func, trigger, run_now)` — one job per kind (`max_instances=1`, `coalesce`),
  recorded in `state_db.jobs` with `last_run` after each run
- `start()` / `shutdown()` — `start` registers the reminder tick (runs once immediately)
- `on_due` contract: must move each reminder to `sent` or `deferred`, or it is handed over again
- Policy and sending live in the injected `on_due` (`ReminderDispatcher`): this layer
  never imports orchestration or interface.
- Depends on: `state_db` (+ injected `on_due`) · Status: **done**

### `proactive/policy.py` — `Policy`
The "whether/when to nudge" engine. Conservative by default.
- `decide(now, kind) -> Decision(allowed, reason, retry_at)` — the single decision point.
  Rules in order: quiet hours (`QUIET_HOURS`) → calendar busy (injected `busy_until(t)`,
  off until #11) → daily limit (`POLICY_MAX_NUDGES_PER_DAY`, `nudge` only)
- `kind`: `reminder` (user asked for it; never dropped by the daily limit) or `nudge`
  (assistant-initiated)
- `should_notify(ctx) -> bool`, `next_window(now, kind)` — wrappers over the same rules
- `record_nudge(now, kind)` — call after sending; updates `last_nudge` and the daily count
  (`meta` table in `state_db`)
- A failing calendar check counts as "free"; malformed config raises at startup
- State flags (#22) are not wired yet
- Depends on: `state_db`, `config` (+ injected `busy_until`) · Status: **done**

## storage

### `storage/vault_repository.py` — `VaultRepository`
Markdown files, the source of truth.
- `read(rel_path)` (raw Markdown), `write(rel_path, content, frontmatter)`,
  `upsert_frontmatter(rel_path, fields)`, `list(subdir)` (all `.md`, hidden folders skipped)
- Status: **done**

### `storage/index_store.py` — `IndexStore`
Embedding index, derived from the vault (local `chromadb` at `INDEX_PATH`, cosine).
- `upsert(note)` — split into ~500-char paragraph chunks (title/summary/tags + body),
  replaces the note's old chunks
- `search(query, k) -> list[SearchHit(path, score, snippet, type, title)]` — best chunk per note
- `remove(path)`, `rebuild(vault)` (drop + re-embed every note), `count()`
- Depends on: an injected `embed(texts, kind)` (`llm_client.embed`) · Status: **done**

### `storage/state_db.py` — `StateDB`
SQLite, operational state only.
- reminders: `add_reminder`, `get_reminder`, `list_reminders`, `reminders_due(now)`,
  `set_reminder_status`, `defer_reminder`, `delete_reminder`
- messages: `save_message(chat_id, role, text)`, `recent_messages(chat_id, limit)`,
  `prune_messages(older_than)`
- jobs: `jobs()`, `upsert_job(kind, schedule)`, `mark_job_run(kind, when)`
- meta: `get_meta`, `set_meta`, `last_nudge()`, `set_last_nudge(when)`
- One thread-safe connection; naive local datetimes stored as ISO text (aware ones rejected)
- Status: **done**

## cross-cutting

### `config.py` — `Config`
- `secret(key)` (raises if missing), `get(key, default)`, `enabled(module)`, `manifest`
- Status: **usable**

### `llm_client.py` — `LLMClient`
- `complete(prompt, tools, system, model, max_tokens)`
- `embed(texts, kind)` — delegates to the local `Embedder` (Anthropic has no embeddings API)
- Status: **done**

### `embeddings.py` — `Embedder`
Local multilingual sentence embeddings on CPU (sentence-transformers). Default model
`paraphrase-multilingual-MiniLM-L12-v2` (Turkish + 50 languages, 384 dims), overridable with
`EMBEDDING_MODEL`. Loaded lazily on first use; note text never leaves the machine.
- `embed(texts, kind="document"|"query") -> list[list[float]]` (unit length)
- Status: **done**

### `models.py`
Domain dataclasses: `Intent`, `Note`, `Bookmark`, `DayMetrics`, `JournalEntry`,
`Reminder`, `StateFlag`. Status: **usable**.

### `main.py`
`build()` wires the Phase 0 path; `main()` starts the gateway.
