# Development

How to set up, write code, and ship it in this repo.

## Prerequisites

- Python 3.11+
- A Telegram bot token (from [@BotFather](https://t.me/BotFather)) and your own chat id
- An Anthropic API key
- (Phase 2+) A Google Cloud project with the Calendar API enabled

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill it in
python -m second_brain.main      # runs the bot
```

## Configuration reference

All configuration comes from the environment (via `.env`, loaded by `Config`). **Secrets
never go in the vault or in git** — `.env` is gitignored.

| Variable | Purpose |
|----------|---------|
| `ANTHROPIC_API_KEY` | Claude access (LLM + embeddings) |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_ALLOWED_CHAT_ID` | Only this chat id may talk to the bot |
| `VAULT_PATH` | Absolute path to the Obsidian vault |
| `GOOGLE_CREDENTIALS_PATH` | OAuth client secret file (Phase 2) |
| `QUIET_HOURS` | e.g. `23:00-08:00`; Policy never nudges in this window |
| `TIMEZONE` | e.g. `Europe/Istanbul` |

Feature switches live in code: `Config.manifest`. A module set to `False` is fully off
(fail-closed) — not partially active.

## Coding conventions

- **Type hints everywhere**; dataclasses for domain objects (`models.py`).
- **Respect the layers.** Never import an upper layer from a lower one (see ARCHITECTURE.md).
- **One writer to the vault.** Only `note_writer`/`journal_writer` call
  `VaultRepository.write`. Everything else reads.
- **Keep the LLM thin.** Prompts assemble in `context_builder`; decisions live in
  orchestration/tools/policy, not buried in prompt strings.
- Line length 100 (`ruff`). Small, single-responsibility functions.

## Adding a new tool (step by step)

This is the most common extension. Say you want a `WeatherTool`:

1. **Create the file** `src/second_brain/tools/weather.py`:
   ```python
   from .base import Tool

   class WeatherTool(Tool):
       name = "weather"

       def __init__(self, llm=None) -> None:
           self.llm = llm

       def run(self, args: dict):
           city = args["city"]
           # ... fetch and return
           raise NotImplementedError
   ```
2. **Register it** in `main.py`'s `build()`: add `WeatherTool()` to the `tools` list passed
   to `Orchestrator`. The orchestrator keys tools by `name`.
3. **Expose it to the LLM.** Add its tool schema where tool definitions are built so
   tool-calling can select it.
4. **Gate it (optional)** behind a `Config.manifest` flag if it should be toggleable.
5. **Test it** — see below.

That's the whole contract: implement `Tool`, register in `build()`, expose to the LLM.

## Testing

```bash
PYTHONPATH=src pytest            # or: pip install -e . && pytest
```

- Unit-test tools and storage with a **temp vault** and an in-memory/temporary SQLite file —
  never touch the real vault in tests.
- Mock `LLMClient` so tests don't hit the network or spend tokens.
- `tests/test_smoke.py` just checks the package wires; add per-module tests as issues close.

## Deployment (Phase 2+)

- Run the single async process 24/7 on a small VPS or a Raspberry Pi (systemd unit or
  Docker).
- Sync the vault between server and laptop with **Git** or **Syncthing**.
- Keep `.env` and `secrets/` on the host only; they are never committed and never synced
  into the vault.

## Git & issue workflow

- Branch per issue: `git switch -c phase-0/note-writer`.
- Reference the issue in the commit: `git commit -m "NoteWriter: write frontmatter (#6)"`.
- Push and open a PR; closing the PR closes the issue.
- The backlog (labels `phase-0` … `phase-5`) is in `ISSUES.md`; open them with
  `scripts/create_issues.sh` (needs the GitHub CLI `gh`).
