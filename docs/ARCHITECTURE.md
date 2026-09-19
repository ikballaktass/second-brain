# Architecture

This is the mental model you need before touching the code. For the formal diagrams
(use case, domain, sequence, class, package) see the design document in this folder.

## Layers

Code is split into six layers. Each lives in its own package under `src/second_brain/`.

```
interface      telegram_gateway              the only way in/out (chat + proactive push)
orchestration  router · orchestrator · context_builder
tools          note_writer · link_capturer · retriever · calendar
               reminder_manager · task_manager · journal_writer · journal_analyzer
proactive      scheduler · policy            makes the assistant act on its own
storage        vault_repository · index_store · state_db
crosscutting   config · llm_client · models
```

**Dependencies flow one way: top → bottom.** `interface` may call `orchestration`;
`orchestration` may call `tools`; everything may call `storage` and the cross-cutting
modules. Nothing lower ever imports something higher. This keeps each layer independently
testable and prevents import cycles — if you find yourself wanting a lower layer to import
an upper one, the responsibility is in the wrong place.

## The three data homes

Every piece of data belongs to exactly one of these. Putting data in the wrong home is the
most common design mistake in this project.

| Home | What goes here | Rebuildable? |
|------|----------------|--------------|
| **Vault** (Markdown) | All user content: notes, bookmarks, journals, tasks, state flags | No — this is the source of truth |
| **Index** (embeddings) | Vectors for semantic search | Yes — regenerate from the vault |
| **state_db** (SQLite) | Operational state: reminder queue, message history, scheduler jobs, last-nudge timestamps | Yes — losing it costs no user content |

**Only `note_writer` and `journal_writer` write to the vault.** Reads are free from
anywhere, but writes go through a single door so the file format stays consistent.

## Inbound message lifecycle

What happens when the user sends a message (see Sequence 1 & 3 in the design doc):

```
Telegram → telegram_gateway._on_message   (auth-check the chat id)
        → orchestrator.handle(message)
        → router.classify(message)         → Intent
        → context_builder.build(message)   (history + related notes + state)
        → orchestrator picks tool(s) via LLM tool-calling
        → tool.run(args)                   (e.g. note_writer → vault_repository.write)
        → orchestrator composes reply
        → telegram_gateway.send(chat_id, reply)
```

## Proactive lifecycle

What makes this project different from a passive PKM (see Sequence 2):

```
scheduler.tick()  (every N seconds, inside the same async process)
   → is there a due reminder / empty journal / morning brief?
   → policy.should_notify(ctx)?   (quiet hours, calendar busy, state flags, rate limit)
        → if NO  → defer to policy.next_window(), user is not disturbed
        → if YES → orchestrator composes the message → telegram_gateway.send(...)
```

The `policy` layer exists so proactivity never becomes spam. Treat "should we message the
user right now?" as a first-class decision with its own rules, not an afterthought.

## Cross-cutting principles

- **Fail-closed modules.** `Config.manifest` switches features on/off. A disabled module
  disappears from tool lists and schedules entirely — it is never half-active.
- **Secrets never touch the vault.** They come only from the environment via `Config`.
  The vault is synced to GitHub; secrets must not be.
- **The LLM is a tool, not the whole app.** `llm_client` is a thin wrapper. Business logic
  (what to save, when to nudge) lives in orchestration/tools/policy, not in prompts.

## Tech stack

| Concern | Choice |
|---------|--------|
| Language | Python 3.11+ (async) |
| Chat channel | Telegram (`python-telegram-bot`) |
| LLM + embeddings | Anthropic Claude (`anthropic`) |
| Vault I/O | Markdown + `python-frontmatter` |
| Index | `chromadb` (local) |
| Operational state | SQLite (`state.db`) |
| Scheduling | `APScheduler` |
| Calendar | Google Calendar API |
