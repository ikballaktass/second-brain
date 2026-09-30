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
- Handler errors are logged with traceback and the user gets a fixed "⚠️ Bir hata oluştu, loglara bak." message (no exception details); an `add_error_handler` hook logs errors outside the handler (network, polling)
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

### `orchestration/energy_suggester.py` — `EnergySuggester`
High-energy topic suggestion; the scheduler's `energy_suggestion` job (every 30 min).
- Sends at most one message a day, 10:00–22:00, while `energy: high` is active and
  `policy.decide(now, "nudge")` allows it (counts toward the daily limit; exam week wins)
- Topic = a note edited in the last 30 days (not README.md, State.md, `journal/`,
  `bookmarks/`) meeting ≥ 1 criterion, judged only from the vault:
  **effort** (`effort`/`difficulty: high`, `zor`/`efor` tag, or ≥ 5 open tasks),
  **near_done** (≥ 3 tasks, ≥ 60 % checked, one still open),
  **frequent** (≥ 3 other notes/journal entries from the last 14 days at ≥ 0.55 similarity;
  needs the index). More criteria first; ties: effort > near_done > frequent > most recent
- Same note not again within 7 days; no qualifying note → no message
- Fixed template per reason, `USER_NAME` optional, path always cited
- Depends on: `vault_repository`, `index_store` (optional), `policy`, `state_db`,
  injected `StateManager.current` · Status: **done**

### `orchestration/journal_nudger.py` — `JournalNudger`
Evening "you haven't journaled today" nudge; the scheduler's `journal_check` job (every 30 min).
- Sends `📓 Bugün günlüğüne henüz bir şey yazmadın. Günün nasıl geçti?` (fixed template) at most
  once per journal day, when: past `JOURNAL_CHECK_TIME` (default 21:00), the day's journal is
  empty, and `policy.decide(now, "nudge")` allows it
- Policy "not now" → retried on the next run; quiet hours end the day's chances; send failure →
  retried. After sending: `record_nudge(kind="nudge")` (counts toward the daily limit) and
  `journal_nudged:<date>` in `state_db.meta`
- Registered when both `journal` and `proactive` are on
- Depends on: `journal_writer`, `policy`, `state_db` · Status: **done**

### `orchestration/context_builder.py` — `ContextBuilder`
Related notes for the LLM, within a budget. Built when `recall` is on.
- `build(message) -> Context` — `index.search` (k=3) → drop scores below `RECALL_MIN_SCORE`
  (default 0.42) → read each note from the **vault** (frontmatter dropped; bookmark summary/url
  kept) → ≤ 1200 chars per note, ≤ 4000 chars total (no scraps under 200) → `Context.related`
- `Context.render()` — `<note path title score>` blocks marked as data, not instructions;
  appended to the orchestrator's system prompt
- Skips short messages and `/commands`; any failure yields an empty context (never raises)
- `RelatedNote.created` — frontmatter `created`/`date`, else the file's mtime
- Not yet: conversation history
- Depends on: `index_store`, `vault_repository` · Status: **done**

### `orchestration/recall_suggester.py` — `RecallSuggester`
"Reminds you of" (UC: surface an older related note). Decided in code, not by the LLM.
- `suggest(context, intent, reply, now) -> str | None` — picks from the `Context` already built
  for the message (so a note saved in this turn never suggests itself). All must hold: intent
  `capture`/`journal`; score ≥ 0.55; note ≥ 7 days old; path not already in the reply; same
  note not suggested in the last 7 days (cooldown in `state_db.meta`, in memory without it)
- Output: `💡 Bu sana şunu hatırlatıyor: "<title>" — <path> (<3 hafta önce>)`, appended to the
  reply by the orchestrator; at most one per message; failures never break the reply
- Depends on: `context_builder`, `state_db` (optional) · Status: **done**

## tools

All tools implement `tools/base.py` → `Tool` (`name`, `run(args)`).

| Module | Class | Responsibility | Phase | Status |
|--------|-------|----------------|-------|--------|
| `note_writer.py` | `NoteWriter` | Write/update Markdown notes; **only** vault writer | 0 | stub |
| `link_capturer.py` | `LinkCapturer` | Fetch URL → summarize + tag → `NoteWriter.save_bookmark` | 1 | done |
| `reminder_manager.py` | `ReminderManager` | Reminder create/list/close + lifecycle (`due`, `mark_sent`, `defer`) on StateDB; registered only when `proactive` is on | 3 | done |
| `trend_report.py` | `TrendReport` | On-request weekly/monthly metric summary (Turkish): average, lowest/highest day, change vs the previous period (`→ benzer` under 0.3); `yetersiz veri` below 3 days (week) / 7 days (month); always ends "tahmin içermez"; no LLM; registered with the journal | 5 | done |
| `state_manager.py` | `StateManager` | `set`/`show` energy, exam week, cycle phase in `State.md` via `NoteWriter.update_state` (defaults: energy +3 days, exam +7 days); `current()` feeds Policy; registered when `proactive` is on | 5 | done |
| `task_manager.py` | `TaskManager` | Tasks with priority | 3 | stub |
| `calendar.py` | `CalendarTool` | Google Calendar read-only: `list` a day's events (LLM) and `busy_until(t)` for Policy; 5-min cache; off until OAuth is set up | 2 | done |
| `retriever.py` | `Retriever` | Semantic search over the vault (`query`, `k` ≤ 10) → `path · score · snippet`; registered when `recall` is on (default) | 4 | done |
| `journal_writer.py` | `JournalWriter` | Append the user's words verbatim (`**HH:MM** — …`) to `journal/YYYY-MM-DD.md` (day starts 04:00); keeps frontmatter; re-indexes the day as `journal`; `has_entries(day)` | 5 | done |
| `journal_analyzer.py` | `JournalAnalyzer` | End-of-day metrics (mood/energy/productivity/stress, 1-5) via one LLM call per day; fills only empty fields through `JournalWriter.annotate` | 5 | done |

`JournalAnalyzer` is intentionally **not** a `Tool` — it is driven by the scheduler (job
`analyze`, daily 04:30 and once at startup), not by user intent. It must fill the fixed metric
schema only and never invent new metrics:
- `parse_metrics` reads exactly the four `DayMetrics` fields; other keys, non-integers and
  values outside 1-5 are dropped
- `analyze(day)` — no LLM call for an empty/missing journal; writes only metrics that are
  still empty (hand edits win) via `JournalWriter.annotate`, which also re-embeds the day
- `run_pending(now)` — the last 7 *finished* journal days not yet marked
  `journal_analyzed:<date>` in `state_db`; marked once analyzed (even with nulls), left
  unmarked on LLM failure so the next run retries
- `JournalWriter.annotate(day, fields)` / `read_day(day)` keep the vault at two writers

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
- State flags: injected `state_flags()` (StateManager.current) — exam week → no `nudge`,
  low energy → at most 1 a day (reason `state_flags`); reminders unaffected
- Depends on: `state_db`, `config` (+ injected `busy_until`) · Status: **done**

## storage

### `storage/vault_repository.py` — `VaultRepository`
Markdown files, the source of truth.
- `read(rel_path)` (raw Markdown), `write(rel_path, content, frontmatter)`,
  `upsert_frontmatter(rel_path, fields)`, `list(subdir)` (all `.md`, hidden folders skipped),
  `modified_at(rel_path)`
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
`Reminder`, `StateFlags` (+ `ENERGY_LEVELS`, `CYCLE_PHASES`; `energy_on(day)`, `exam_on(day)`,
`from_frontmatter`). Status: **usable**.

### `main.py`
`build()` wires the Phase 0 path; `main()` starts the gateway.
