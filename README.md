# Second Brain

A proactive, single-user personal AI assistant. You chat with it on Telegram; it
captures ideas, bookmarks, journals and tasks into an **Obsidian vault** (Markdown),
indexes them semantically, and — unlike a passive PKM — messages you on its own
initiative at appropriate times.

Full design rationale (use cases, domain model, sequence/class/package diagrams) is in
[`docs/Second-Brain-Design-Document.docx`](docs/Second-Brain-Design-Document.docx).

## Architecture (layers)

```
interface      telegram_gateway            chat + proactive push channel
orchestration  router · orchestrator · context_builder
tools          note_writer · link_capturer · retriever · calendar
               reminder_manager · task_manager · journal_writer · journal_analyzer
proactive      scheduler · policy          the "not passive" part
storage        vault_repository (source of truth) · index_store (derived) · state_db (SQLite)
crosscutting   config · llm_client · sync
```

Dependencies flow one way, top → bottom. The Markdown vault is the single source of
truth; the embedding index is derived; `state_db` holds only operational state.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: py -3 -m venv .venv
pip install -r requirements.txt
cp .env.example .env        # fill in your keys and vault path
python -m second_brain.main # runs the bot (Phase 0)
```

## Roadmap

| Phase | Scope |
|-------|-------|
| 0 | Backbone (MVP): message → LLM reply → Markdown note |
| 1 | Capture: bookmark flow (fetch → summarize → write) |
| 2 | Always-on + Google Calendar |
| 3 | Proactive reminders (rule-based policy) |
| 4 | Semantic recall ("you saved this weeks ago") |
| 5 | Adaptive timing (journal → metrics → policy) |

See the open issues for the task backlog.

## License

MIT — see [LICENSE](LICENSE).
