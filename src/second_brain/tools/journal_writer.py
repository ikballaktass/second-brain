"""Opens/appends today's journal note (Phase 5). One of the two vault writers.

Each entry is the user's own words, verbatim, stamped with the time:

    **14:05** — Bugün sınav vardı, kötü geçti ama akşam arkadaşlarla iyi vakit geçirdim.

A journal day starts at 04:00, so writing at 01:30 about the evening goes into the
day that just ended rather than into tomorrow's empty file. Existing frontmatter
(including metrics the user corrected by hand) is kept on every append; metric
fields themselves are filled later by JournalAnalyzer (#21).
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta
from typing import Callable

import frontmatter

from ..models import JournalEntry
from ..storage.vault_repository import VaultRepository
from .base import Tool

logger = logging.getLogger(__name__)

JOURNAL_DIR = "journal"
DAY_START = time(4, 0)


def journal_day(now: datetime) -> date:
    """The journal day `now` belongs to (days run from 04:00 to 04:00)."""
    return (now - timedelta(hours=DAY_START.hour, minutes=DAY_START.minute)).date()


def journal_path(day: date) -> str:
    return f"{JOURNAL_DIR}/{day.isoformat()}.md"


class JournalWriter(Tool):
    name = "journal_writer"
    description = (
        "Append to the user's journal for today. Use when the user narrates their day, "
        "feelings, experiences or the people they met. Pass their own words verbatim as "
        "text; do not summarize or rephrase."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The user's journal entry, verbatim"},
        },
        "required": ["text"],
    }

    def __init__(
        self,
        vault: VaultRepository,
        index=None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.vault = vault
        self.index = index
        self.clock = clock
        # Read-modify-write: two messages handled in parallel must not drop an entry.
        self._lock = threading.Lock()

    def run(self, args: dict) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("'text' is required and cannot be empty")
        path = self.append(text.strip())
        return f"Journal → {path}"

    def append(self, text: str, now: datetime | None = None) -> str:
        """Add one timestamped entry to the day's journal; return its vault path."""
        now = now or self.clock()
        day = journal_day(now)
        path = journal_path(day)
        entry = f"**{now:%H:%M}** — {text}"

        with self._lock:
            if self.vault.exists(path):
                post = frontmatter.loads(self.vault.read(path))
                metadata, body = dict(post.metadata), post.content.strip()
            else:
                metadata, body = {"type": "journal", "date": day.isoformat()}, ""
            body = f"{body}\n\n{entry}" if body else entry
            self.vault.write(path, body, metadata)

        self._index(day, path, body)
        return path

    def has_entries(self, day: date) -> bool:
        """True if the day's journal exists and has any text below the frontmatter."""
        path = journal_path(day)
        if not self.vault.exists(path):
            return False
        return bool(frontmatter.loads(self.vault.read(path)).content.strip())

    def _index(self, day: date, path: str, body: str) -> None:
        """Re-embed the whole day; failures never undo the save."""
        if self.index is None:
            return
        entry = JournalEntry(
            title=f"Günlük {day.isoformat()}", content=body, path=path,
            created=datetime.combine(day, time.min), day=day,
        )
        try:
            self.index.upsert(entry)
        except Exception:
            logger.warning("Index upsert failed for %s", path, exc_info=True)
