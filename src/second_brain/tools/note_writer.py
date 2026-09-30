"""Writes/updates Markdown notes with frontmatter + tags. The ONLY writer to the vault."""
from __future__ import annotations

import logging
import re
from datetime import datetime

from ..models import Bookmark
from ..storage.vault_repository import VaultRepository
from .base import Tool

logger = logging.getLogger(__name__)

INBOX_DIR = "00-Gelen"
BOOKMARKS_DIR = "bookmarks"
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

    def __init__(self, vault: VaultRepository, index=None) -> None:
        self.vault = vault
        self.index = index

    def run(self, args: dict) -> str:
        """Write the note and return its vault-relative path."""
        title = _require_str(args, "title")
        content = _require_str(args, "content")
        if not content.strip():
            raise ValueError("content is empty")

        safe_title = _sanitize_title(title)

        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d-%H%M%S")

        rel_path = self._unique_path(INBOX_DIR, f"{stamp} {safe_title}")

        frontmatter = {
            "created": now.isoformat(timespec="seconds"),
            "source": "telegram",
        }

        self.vault.write(rel_path, content, frontmatter)

        return rel_path

    def save_bookmark(self, bookmark: Bookmark) -> str:
        """Write a bookmark note (DATA_MODEL shape) and return its vault-relative path.

        Also upserts it into the index when one is configured. An index failure is
        logged, not raised: the vault is the source of truth and the index is rebuildable.
        """
        if not bookmark.url.strip():
            raise ValueError("bookmark url is empty")

        safe_title = _sanitize_title(bookmark.title or bookmark.url)
        stamp = bookmark.created.strftime("%Y-%m-%d-%H%M%S")
        rel_path = self._unique_path(BOOKMARKS_DIR, f"{stamp} {safe_title}")

        frontmatter = {"type": "bookmark", "url": bookmark.url}
        if bookmark.source:
            frontmatter["source"] = bookmark.source
        frontmatter["tags"] = list(bookmark.tags)
        frontmatter["summary"] = bookmark.summary
        frontmatter["created"] = bookmark.created.isoformat(timespec="seconds")

        self.vault.write(rel_path, bookmark.content, frontmatter)
        bookmark.path = rel_path

        if self.index is not None:
            try:
                self.index.upsert(bookmark)
            except Exception:
                logger.warning("Index upsert failed for %s", rel_path, exc_info=True)

        return rel_path

    def _unique_path(self, folder: str, stem: str) -> str:
        """Return f"{folder}/{stem}.md", adding -2, -3, ... if taken."""
        candidate = f"{folder}/{stem}.md"
        n = 2
        while self.vault.exists(candidate):
            candidate = f"{folder}/{stem}-{n}.md"
            n += 1
        return candidate