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
| `ANTHROPIC_API_KEY` | Claude access (LLM; embeddings are local) |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_ALLOWED_CHAT_ID` | Only this chat id may talk to the bot |
| `VAULT_PATH` | Absolute path to the Obsidian vault |
| `GOOGLE_CREDENTIALS_PATH` | OAuth client secret file (Phase 2) |
| `GOOGLE_TOKEN_PATH` | OAuth token written by `scripts/google_auth.py` (default `secrets/google_token.json`) |
| `GOOGLE_CALENDAR_ID` | Calendar to read (default `primary`) |
| `QUIET_HOURS` | e.g. `23:00-08:00`; Policy never nudges in this window (default `23:00-08:00`) |
| `POLICY_MAX_NUDGES_PER_DAY` | Daily cap on assistant-initiated nudges (default `3`); user reminders are exempt |
| `TIMEZONE` | e.g. `Europe/Istanbul` |
| `STATE_DB_PATH` | SQLite file for operational state (default `state.db`) |
| `INDEX_PATH` | Derived embedding index (default `.index`, must be outside the vault) |
| `EMBEDDING_MODEL` | sentence-transformers model (default multilingual MiniLM) |
| `SCHEDULER_INTERVAL_S` | Seconds between proactive ticks (default `60`) |

Feature switches live in code: `Config.manifest`. A module set to `False` is fully off
(fail-closed) — not partially active.

## Google Calendar (optional)

The calendar module is **off** by default (`Config.manifest["calendar"] = False`). To turn it on:

1. In Google Cloud Console: create a project, enable the **Google Calendar API**, configure the
   OAuth consent screen (add yourself as a test user), and create an OAuth client of type
   **Desktop app**. Download its JSON to `secrets/google_credentials.json`.
2. On a machine with a browser run `PYTHONPATH=src python scripts/google_auth.py`. It asks only
   for **read-only** calendar access and writes `secrets/google_token.json` (mode 0600).
   Headless: `--no-browser --port 8765` and forward the port with `ssh -L 8765:localhost:8765`.
3. Set `"calendar": True` in `Config.manifest`. With the flag on, a missing or unusable token
   stops the bot at startup with a message saying what to do.

Once on, the LLM can list a day's events, and Policy holds reminders back until the current
(timed, busy) event ends. `secrets/` is gitignored and must stay outside the vault.

## Semantic recall (optional)

The recall module is **on** (`Config.manifest["recall"] = True`). It needs no API key:
embeddings are computed locally with `sentence-transformers`. On a new machine:

1. Install the CPU build of torch before the requirements (much smaller than the default CUDA
   build): `pip install torch --index-url https://download.pytorch.org/whl/cpu`.
2. Build the index once: `PYTHONPATH=src python scripts/rebuild_index.py --query "test"`.
   The first run downloads the model (~470 MB, into `HF_HOME`, default `~/.cache/huggingface`).
3. New notes and bookmarks are indexed on save, and the LLM gets the `retriever` tool. To turn
   recall off, set `"recall": False` in `Config.manifest`.

Re-run the script whenever notes were edited outside the bot (e.g. in Obsidian) or after
changing `EMBEDDING_MODEL`. Tests use a fake embedder; `RUN_MODEL_TESTS=1 pytest` also runs
one check against the real model.

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

See [DEPLOY.md](DEPLOY.md): systemd service on a VPS / Raspberry Pi, vault sync over git
(`deploy/vault-sync.sh` + timer) or Syncthing, and how secrets stay off the vault. The bot
refuses to start if `.env`, `state.db` or a Google secret file resolves inside `VAULT_PATH`.

## Git & issue workflow

- Branch per issue: `git switch -c phase-0/note-writer`.
- Reference the issue in the commit: `git commit -m "NoteWriter: write frontmatter (#6)"`.
- Push and open a PR; closing the PR closes the issue.
- The backlog (labels `phase-0` … `phase-5`) is in `ISSUES.md`; open them with
  `scripts/create_issues.sh` (needs the GitHub CLI `gh`).
