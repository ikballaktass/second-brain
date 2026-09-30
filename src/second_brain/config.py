"""Configuration and secret management (cross-cutting).

Secrets come only from the environment / .env — NEVER from the vault.
`manifest` is the fail-closed module switch: a disabled module is absent
everywhere (tools, prompts, sync), not merely hidden.
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import find_dotenv, load_dotenv

ENV_FILE = find_dotenv()
load_dotenv(ENV_FILE)

# Files that hold secrets, operational state or the derived index, with the default each
# module uses. None of them may live inside the vault: the vault is synced to git.
OFF_VAULT_PATHS: dict[str, str] = {
    "STATE_DB_PATH": "state.db",
    "GOOGLE_CREDENTIALS_PATH": "secrets/google_credentials.json",
    "GOOGLE_TOKEN_PATH": "secrets/google_token.json",
    "INDEX_PATH": ".index",
}


class Config:
    manifest: dict[str, bool] = {
        "capture": True,
        "journal": True,
        "calendar": False,   # Phase 2
        "proactive": True,   # Phase 3: reminders + scheduler + send path
        "recall": True,      # Phase 4: local embeddings + retriever (index: scripts/rebuild_index.py)
    }

    @staticmethod
    def secret(key: str) -> str:
        val = os.getenv(key)
        if not val:
            raise RuntimeError(f"Missing secret: {key}")
        return val

    @staticmethod
    def get(key: str, default: str | None = None) -> str | None:
        return os.getenv(key, default)

    @classmethod
    def enabled(cls, module: str) -> bool:
        return cls.manifest.get(module, False)


def assert_outside_vault(vault_path: str) -> None:
    """Raise if .env, state.db, the index or a Google secret resolves inside the vault.

    Paths are resolved like the modules resolve them (relative to the working
    directory, `~` expanded, symlinks followed), so `..` tricks and links are caught.
    """
    vault = Path(vault_path).expanduser().resolve()
    candidates = {name: Config.get(name, default) for name, default in OFF_VAULT_PATHS.items()}
    if ENV_FILE:
        candidates[".env"] = ENV_FILE
    for name, raw in candidates.items():
        path = Path(raw).expanduser().resolve()
        if path == vault or path.is_relative_to(vault):
            raise RuntimeError(
                f"{name} ({path}) is inside the vault ({vault}). The vault is synced to git; "
                "move this file outside it."
            )
