#!/usr/bin/env bash
# Create the issues found in the 2026-09-30 readiness review.
# Prereq: `gh auth login` done; run from inside the repo.
# Skips any issue whose exact title already exists (open or closed).
set -euo pipefail

gh label create 'bug'        --color d73a4a --force >/dev/null 2>&1 || true
gh label create 'hardening'  --color 0052cc --force >/dev/null 2>&1 || true
gh label create 'type-chore' --color ededed --force >/dev/null 2>&1 || true
gh label create 'type-feature' --color c2e0c6 --force >/dev/null 2>&1 || true

existing="$(gh issue list --state all --limit 500 --json title --jq '.[].title')"

create() {  # create <title> <labels,comma,separated>  (body on stdin)
  local title="$1" labels="$2"
  if grep -Fxq "$title" <<<"$existing"; then
    echo "skip (exists): $title"
    cat >/dev/null
    return
  fi
  gh issue create --title "$title" --label "$labels" --body-file -
}

create "Use the configured TIMEZONE for every clock read" "bug,hardening" <<'EOF'
`TIMEZONE=Europe/Istanbul` is in `.env.example` but is never read. Every module uses
naive `datetime.now()` / `date.today()`, i.e. the **server's** local time. The Oracle
host defaults to UTC, so reminders, quiet hours, the 21:00 journal nudge, the 04:30
analyzer run and "today's" journal file are all off by 3 hours.

- [ ] `Config.tz()` returns `ZoneInfo(TIMEZONE)` (fail-closed on an invalid name)
- [ ] one injectable clock (`now()` in local tz) used by orchestrator, policy, scheduler,
      reminder_manager, state_db, journal_*, calendar, state_manager, trend_report, models
- [ ] APScheduler created with `timezone=` so CronTrigger fires in local time
- [ ] reminder `due` parsed/stored consistently (existing naive rows keep working)
- [ ] tests: behaviour is identical whether the host TZ is UTC or Europe/Istanbul
- [ ] DEPLOY.md: note `timedatectl` is no longer required

Modules: `config.py` + every caller of `datetime.now()` (ruff DTZ005 lists them)
EOF

create "Orchestrator: send tool results back to the LLM for the final reply" "hardening,type-feature" <<'EOF'
`Orchestrator.handle()` calls the LLM once and returns the raw tool output as the reply,
so the user sees strings like `Reminder set → #1 · 2026-09-30 19:10 · ilaç al (pending)`
or, for `retriever`, a `path · 0.53 · snippet` list instead of an answer. On a tool error
(e.g. `due="dün"`) the LLM never gets a chance to fix its call.

- [ ] proper tool-use loop: assistant `tool_use` → `tool_result` blocks → next call
- [ ] errors returned as `tool_result` with `is_error=true` so the model can retry
- [ ] max iterations (e.g. 4) to bound cost/latency
- [ ] final reply written by the model in the user's language
- [ ] `LLMClient.complete()` accepts a message list (not only a single prompt)
- [ ] tests with a fake LLM: single tool, chained tools, tool error → retry, loop limit

Module: `orchestration/orchestrator.py`, `llm_client.py`
EOF

create "Short-term conversation history" "hardening,type-feature" <<'EOF'
Every message is sent to the LLM alone, so follow-ups ("evet kaydet", "saati 11 yap",
"onu iptal et") have no context. `StateDB.messages` and `save_message()` exist but are
never called.

- [ ] save user + assistant turns (text only) via `save_message()`
- [ ] include the last N turns / last X minutes in the LLM call
- [ ] retention: prune old rows (state_db is operational state, not an archive)
- [ ] works (in memory) when the proactive module / state_db is off
- [ ] tests: follow-up resolves against the previous turn; pruning

Modules: `storage/state_db.py`, `orchestration/orchestrator.py`, `main.py`
EOF

create "Drop the per-message Router LLM call (latency)" "hardening" <<'EOF'
Every message costs two sequential LLM calls: Router (intent) then the main call. The
intent is only used by the recall hint; the `TODO(phase-1+)` branch never landed. This is
a large share of the perceived slowness on the server.

- [ ] measure: log per-call latency for router / main / embeddings
- [ ] remove the router call, or derive intent without an extra round trip
      (slash commands still short-circuit)
- [ ] recall suggester keeps working (update its intent input)
- [ ] remove the stale TODO
- [ ] tests updated

Modules: `orchestration/router.py`, `orchestration/orchestrator.py`,
`orchestration/recall_suggester.py`
EOF

create "Test suite for the core modules" "hardening" <<'EOF'
`tests/` has one test (`__version__ == "0.1.0"`). CLAUDE.md requires tests with every
change, but none are in the repo, so regressions on the server go unnoticed.

- [ ] unit tests: config (fail-closed, assert_outside_vault), vault_repository, note_writer,
      journal_writer, reminder_manager lifecycle, state_db, policy (quiet hours, rate limit,
      busy calendar), scheduler tick, reminder_dispatcher
- [ ] orchestrator tests with a fake LLM (no network)
- [ ] shared fixtures: temp vault, temp state.db, fixed clock
- [ ] `pytest -q` runs offline and fast; add pytest-asyncio to requirements-dev
- [ ] optional: GitHub Actions workflow running pytest + ruff on push

Directory: `tests/`
EOF

create "TaskManager: tasks with priority" "type-feature" <<'EOF'
`tools/task_manager.py` still raises `NotImplementedError` and is not wired, so the
"tasks" part of the capture scope does not exist.

- [ ] decide storage: tasks as vault notes (user content → vault, not state_db)
- [ ] actions: create (title, priority, optional due), list (open, by priority), complete
- [ ] only note_writer writes to the vault (reuse it)
- [ ] wired behind a `Config.manifest` switch; system prompt line added
- [ ] tests

Module: `tools/task_manager.py`
EOF

create "Turkish user-facing error messages" "hardening" <<'EOF'
Fallback and tool-error texts shown to the user are English
("Something went wrong. Please try again.", "Could not run …", "Tool … failed to run.").

- [ ] user-facing fallback texts in Turkish, one place (e.g. `prompts.py`)
- [ ] distinguish: LLM unreachable / rate limited vs. internal error
- [ ] details stay in the logs, not in the chat
- [ ] tests

Modules: `main.py`, `orchestration/orchestrator.py`
EOF

create "Split replies longer than Telegram's 4096-character limit" "bug" <<'EOF'
`send_message()` sends the text as is. Telegram rejects messages over 4096 characters,
so a long reply (trend report, many search hits, long summary) fails and the user gets
nothing.

- [ ] split on paragraph/line boundaries into ≤4096-char chunks, sent in order
- [ ] applies to both replies and proactive messages
- [ ] tests (exactly 4096, 4097, one huge line)

Module: `interface/telegram_gateway.py`
EOF

create "Reply to non-text messages instead of ignoring them" "hardening" <<'EOF'
Only `filters.TEXT & ~filters.COMMAND` is handled. Photos, voice notes, documents,
stickers and slash commands (e.g. `/start`) get no answer at all, which looks like the
bot is down.

- [ ] short Turkish reply for unsupported message types
- [ ] `/start` and `/help`: what the bot can do
- [ ] photo/document *captions* are handled as text
- [ ] same chat-id auth check for every handler
- [ ] tests

Module: `interface/telegram_gateway.py`
EOF

create "Repo cleanup: stray files, .env.example, ruff" "type-chore" <<'EOF'
- [ ] remove `Pasted image.png` from the repo root
- [ ] remove or fix `scripts/Smoke` (unquoted strings; not valid Python)
- [ ] `.env.example`: `ANTHROPIC_API_KEY=` empty (currently `test`)
- [ ] `ruff check src` clean or rules configured in pyproject (57 findings; the DTZ ones
      are covered by the TIMEZONE issue)
- [ ] `create_issues.sh`: bodies use `\n` inside double quotes, which gh does not expand
EOF

echo 'Done. See: gh issue list'
