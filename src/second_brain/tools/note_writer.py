"""Writes/updates Markdown notes with frontmatter + tags. The ONLY writer to the vault."""
from __future__ import annotations
from .base import Tool


class NoteWriter(Tool):
    name = "note_writer"

    def __init__(self, vault=None) -> None:
        self.vault = vault

    def run(self, args: dict):
        raise NotImplementedError
