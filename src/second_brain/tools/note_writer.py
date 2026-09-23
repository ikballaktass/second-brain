"""Writes/updates Markdown notes with frontmatter + tags. The ONLY writer to the vault."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import Tool

INBOX_DIR = "00-Gelen"


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

    def __init__(self, vault) -> None:
        self.vault = vault

    def run(self, args: dict) -> str:
        title = args["title"]
        content = args["content"]

        safe_title = title.replace("/", "-").strip()

        now = datetime.now()
        stamp = now.strftime("%Y-%m-%d-%H%M%S")

        rel_path = f"{INBOX_DIR}/{stamp} {safe_title}.md"

        frontmatter = {
            "created": now.isoformat(timespec="seconds"),
            "source": "telegram",
        }

        self.vault.write(rel_path, content, frontmatter)

        return f"Saved: {rel_path}"
