"""Vault repository — Markdown files, the SOURCE OF TRUTH (storage layer)."""
from __future__ import annotations

from pathlib import Path

from frontmatter import Post, dumps


class VaultRepository:
    def __init__(self, vault_path: str) -> None:
        if not vault_path:
            raise ValueError("Vault path cannot be empty.")
        self.root = Path(vault_path).expanduser().resolve()
    
    def _resolve(self, rel_path: str) -> Path:
        full = (Self.root / rel_path).resolve()
        if not full.is_relative_to(self.root):
            raise ValueError(f"Path {rel_path} is outside the vault root {self.root}.")
        return full

    def read(self, rel_path: str) -> str:
        raise NotImplementedError

    def write(self, rel_path: str, content: str, frontmatter: dict | None = None) -> None:
        """Write a note (with YAML frontmatter). The only write path to the vault."""
        full = self._resolve(rel_path)
        full.parent.mkdir(parents=True, exist_ok=True)

        post = Post(content)
        post.metadata.update(frontmatter or {})

        text = dumps(post, sort_keys=False) + "\n"
        full.write_text(text, encoding="utf-8")

    def upsert_frontmatter(self, rel_path: str, fields: dict) -> None:
        raise NotImplementedError

    def list(self, subdir: str = "") -> list[str]:
        raise NotImplementedError
