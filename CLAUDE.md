# Second Brain — Claude Code guide

Proactive single-user personal AI assistant. Chat via Telegram; captures ideas,
bookmarks, journals and tasks into an Obsidian Markdown vault; indexes them
semantically; and proactively messages the user at appropriate times.
Python 3.11+. Code, comments, commits and docs are in English.
**Talk to the user in Turkish.**

## Read before touching code
- `docs/ARCHITECTURE.md`, `docs/MODULES.md`, `docs/DATA_MODEL.md`, `docs/DEVELOPMENT.md`
- The GitHub issue you are working on (`gh issue view <n> --comments`)

## Architecture rules (do not break)
Six layers, dependencies flow one way only (top → bottom):
1. `interface` — telegram_gateway is the ONLY in/out channel (chat + proactive push)
2. `orchestration` — router, orchestrator, context_builder
3. `tools` — every tool implements the Tool interface
4. `proactive` — scheduler, policy (quiet hours, calendar, state flags, rate limit)
5. `storage` — vault_repository, index_store, state_db
6. `crosscutting` — config, llm_client, models (usable from any layer)

- The vault (Markdown) is the source of truth. Only `note_writer` and `journal_writer` write to it.
- The index (chromadb) is derived and must be rebuildable from the vault.
- `state_db` (SQLite) holds operational state only — never user content.
- Secrets come from config/env, never from the vault, never committed. Config fails closed.
- No new dependency without saying why and asking first.

## Working style (important)
- **Plan first.** Before any edit, give a numbered plan in Turkish and wait for approval.
- **Write complete, finished code** — no skeletons, no TODO placeholders.
- Keep changes scoped to the issue. If you spot something outside it, mention it instead of fixing it.
- Every change comes with tests (pytest). Run them before saying a step is done.

## Git
- Branch per issue: `issue-<n>-<short-slug>`, created from an up-to-date `main`.
- Small commits, conventional style (`feat:`, `fix:`, `test:`, `docs:`).
- PR body ends with `Closes #<n>`. Ask before pushing or opening the PR.

## Commands
- Tests: `pytest -q`
- Lint/format: see `docs/DEVELOPMENT.md`
