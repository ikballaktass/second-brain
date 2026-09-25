"""Writes/updates Markdown notes with frontmatter + tags. The ONLY writer to the vault."""
from __future__ import annotations

import re
from datetime import datetime

from ..storage.vault_repository import VaultRepository
from .base import Tool

INBOX_DIR = "00-Gelen"
MAX_TITLE_LEN = 80
_FORBIDDEN = re.compile(r'[<>:"/\\|?*#^\[\]]')


def _sanitize_title(title: str) -> str:
    """Turn an LLM-given title into a safe Obsidian filename."""
    title = _FORBIDDEN.sub("-", title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip(". ")
    title = title[:MAX_TITLE_LEN].strip(". ")
    return title or "untitled"


def _require_str(args: dict, key: str) -> str:
    """Return args[key] if it is a string, else raise ValueError."""
    if key not in args:
        raise ValueError(f"missing '{key}'")
    value = args[key]
    if not isinstance(value, str):
        raise ValueError(f"'{key}' must be a string")
    return value


class NoteWriter(Tool):
    name = "note_writer"
    description = (
        "Save a new note to the user's Obsidian vault. Use when the user asks to save, note down, or remember something."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short note title"},
            "content": {"type": "string", "description": "Note content in Markdown"},
        },
        "required": ["title", "content"],
    }

    def __init__(self, vault: VaultRepository) -> None:
        self.vault = vault

    def run(self, args: dict) -> str:
        """Write the note and return its vault-relative path."""
        title = _require_str(args, "title")
        content = _require_str(args, "content")
        if not content.strip():
            raise ValueError("content is empty")

        safe_title = _sanitize_title(title)

        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d-%H%M%S")

        rel_path = self._unique_path(f"{stamp} {safe_title}")

        frontmatter = {
            "created": now.isoformat(timespec="seconds"),
            "source": "telegram",
        }

        self.vault.write(rel_path, content, frontmatter)

        return rel_path

    def _unique_path(self, stem: str) -> str:
        """Return f"{INBOX_DIR}/{stem}.md", adding -2, -3, ... if taken."""
        candidate = f"{INBOX_DIR}/{stem}.md"
        n = 2
        while self.vault.exists(candidate):
            candidate = f"{INBOX_DIR}/{stem}-{n}.md"
            n += 1
        return candidate