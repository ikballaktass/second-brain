"""Opens/appends today's journal note (Phase 5)."""
from __future__ import annotations
from .base import Tool


class JournalWriter(Tool):
    name = "journal_writer"

    def __init__(self, vault=None) -> None:
        self.vault = vault

    def run(self, args: dict):
        raise NotImplementedError
