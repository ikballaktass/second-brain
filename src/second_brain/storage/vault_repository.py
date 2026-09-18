"""Vault repository — Markdown files, the SOURCE OF TRUTH (storage layer)."""
from __future__ import annotations
from pathlib import Path


class VaultRepository:
    def __init__(self, vault_path: str) -> None:
        self.root = Path(vault_path)

    def read(self, rel_path: str) -> str:
        raise NotImplementedError

    def write(self, rel_path: str, content: str, frontmatter: dict | None = None) -> None:
        """Write a note (with YAML frontmatter). The only write path to the vault."""
        raise NotImplementedError

    def upsert_frontmatter(self, rel_path: str, fields: dict) -> None:
        raise NotImplementedError

    def list(self, subdir: str = "") -> list[str]:
        raise NotImplementedError
