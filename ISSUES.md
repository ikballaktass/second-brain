# Task Backlog

Issues grouped by phase. Open them with `scripts/create_issues.sh` (needs GitHub CLI `gh`), or copy manually.


## Phase 0

### Set up project skeleton and config loading
*Module:* `config.py`  ·  *Label:* `phase-0`

Bootstrap the package layout and environment-based config.
- [ ] `.env` loading via python-dotenv
- [ ] `Config.secret()` raises on missing keys
- [ ] `Config.manifest` fail-closed module switches
- [ ] `python -m second_brain.main` runs (even if it only logs)

### Implement LLMClient.complete() (Claude wrapper)
*Module:* `llm_client.py`  ·  *Label:* `phase-0`

Thin wrapper over the Anthropic API.
- [ ] `complete(prompt, tools)` returns text
- [ ] tool-calling supported
- [ ] key read from `ANTHROPIC_API_KEY`

### VaultRepository.write() with YAML frontmatter
*Module:* `storage/vault_repository.py`  ·  *Label:* `phase-0`

The single write path to the Obsidian vault (source of truth).
- [ ] write Markdown + frontmatter (python-frontmatter)
- [ ] `upsert_frontmatter()` updates fields in place
- [ ] paths resolved under `VAULT_PATH`

### TelegramGateway: receive + send (single-user auth)
*Module:* `interface/telegram_gateway.py`  ·  *Label:* `phase-0`

Bidirectional channel; only the allowed chat id is served.
- [ ] long-polling start()
- [ ] `_on_message` rejects unknown chat ids
- [ ] `send(chat_id, text)` works (used later by proactive)

### Orchestrator minimal path (message -> LLM -> note)
*Module:* `orchestration/orchestrator.py`  ·  *Label:* `phase-0`

Wire the shortest working flow end to end.
- [ ] `handle(message)` calls LLM and replies
- [ ] can invoke NoteWriter to save a note
- [ ] smoke-tested via Telegram

### NoteWriter tool
*Module:* `tools/note_writer.py`  ·  *Label:* `phase-0`

First Tool implementation; writes an atomic note.
- [ ] implements `Tool.run(args)`
- [ ] delegates to VaultRepository
- [ ] returns the created note path


## Phase 1

### Router intent classification
*Module:* `orchestration/router.py`  ·  *Label:* `phase-1`

Classify incoming messages into `Intent` with a cheap model.
- [ ] returns one of the `Intent` enum values
- [ ] handles capture/query/journal/reminder

### LinkCapturer: fetch + summarize
*Module:* `tools/link_capturer.py`  ·  *Label:* `phase-1`

Fetch a URL, extract content, summarize via LLM.
- [ ] fetch with httpx + parse with BeautifulSoup
- [ ] LLM summary
- [ ] graceful fallback when fetch fails (store raw link + note)

### Bookmark flow end-to-end
*Module:* `tools/note_writer.py`  ·  *Label:* `phase-1`

UC2: URL + 'look later' -> tagged bookmark in vault.
- [ ] frontmatter (url, summary, tags, created)
- [ ] auto-tagging
- [ ] added to index (stub ok until Phase 4)


## Phase 2

### StateDB (SQLite) schema + CRUD
*Module:* `storage/state_db.py`  ·  *Label:* `phase-2`

Operational state only (not user content).
- [ ] tables: reminders, messages, jobs, last_nudge
- [ ] `reminders_due()`, `save_message()`, `jobs()`

### CalendarTool: Google Calendar OAuth + read
*Module:* `tools/calendar.py`  ·  *Label:* `phase-2`

Read events to inform scheduling windows.
- [ ] OAuth flow (google-auth-oauthlib)
- [ ] list events for a day
- [ ] enabled via `Config.manifest['calendar']`

### Deploy: always-on host + vault sync
*Module:* `README.md`  ·  *Label:* `phase-2`

Run 24/7 on a VPS/Pi; sync vault to laptop.
- [ ] Dockerfile / systemd unit
- [ ] vault sync (Git or Syncthing) documented
- [ ] secrets kept off the vault


## Phase 3

### Scheduler background loop (APScheduler)
*Module:* `proactive/scheduler.py`  ·  *Label:* `phase-3`

Periodic tick that drives proactive behavior.
- [ ] tick() checks due reminders
- [ ] runs inside the same async process

### Policy engine (quiet hours, rate limit, calendar-aware)
*Module:* `proactive/policy.py`  ·  *Label:* `phase-3`

Decide whether/when to nudge; conservative by default.
- [ ] quiet hours from `QUIET_HOURS`
- [ ] skip when calendar busy
- [ ] rate limit per day
- [ ] `next_window()`

### ReminderManager CRUD + lifecycle
*Module:* `tools/reminder_manager.py`  ·  *Label:* `phase-3`

Reminder states: pending -> deferred -> sent -> closed.
- [ ] create/list/close
- [ ] persisted in StateDB

### Proactive send path (scheduler -> policy -> gateway)
*Module:* `proactive/scheduler.py`  ·  *Label:* `phase-3`

UC7 wired end to end.
- [ ] due reminder -> policy check -> send
- [ ] deferral when not appropriate


## Phase 4

### IndexStore embeddings + Retriever
*Module:* `storage/index_store.py`  ·  *Label:* `phase-4`

Semantic recall over the vault (derived, rebuildable).
- [ ] embed notes via LLMClient.embed
- [ ] `search(query, k)`
- [ ] Retriever tool wraps it

### ContextBuilder retrieval integration
*Module:* `orchestration/context_builder.py`  ·  *Label:* `phase-4`

Feed related notes into the LLM context.
- [ ] pull top-k related notes
- [ ] token budget management

### 'Reminds you of' recall suggestion
*Module:* `orchestration/orchestrator.py`  ·  *Label:* `phase-4`

Surface older related notes during conversation.
- [ ] detect topical overlap
- [ ] cite the source note path


## Phase 5

### JournalWriter + journal reminder job
*Module:* `tools/journal_writer.py`  ·  *Label:* `phase-5`

Append to today's journal; nudge if empty by evening.
- [ ] `journal/YYYY-MM-DD.md` create/append
- [ ] scheduler job checks emptiness
- [ ] nudge routed through Policy

### JournalAnalyzer: fixed metric schema
*Module:* `tools/journal_analyzer.py`  ·  *Label:* `phase-5`

Extract mood/energy/productivity/stress (1-5) at end of day.
- [ ] FIXED schema only (no invented metrics)
- [ ] write to frontmatter as editable suggestions
- [ ] re-embed the note

### State flags -> policy inputs
*Module:* `models.py`  ·  *Label:* `phase-5`

User-reported energy/cycle/exam feed timing.
- [ ] parse 'exam week' / 'low energy' etc.
- [ ] stored as StateFlag in a vault note
- [ ] Policy reads them

### Descriptive trend reporting (no prediction)
*Module:* `tools/journal_analyzer.py`  ·  *Label:* `phase-5`

Backward-looking summaries only, on request.
- [ ] weekly/monthly metric trends
- [ ] explicitly avoid forecasting until enough data
