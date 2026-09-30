"""'Reminds you of' — surface an older related note after a reply (orchestration layer).

Decided in code, not by the LLM: a prompt instruction would fire unpredictably.
It reuses the related notes ContextBuilder already found for this message (built
before any tool runs, so a note saved in this very turn can never suggest itself).

All of these must hold, so it stays rare and useful:
- the message is a capture or a journal entry (a query already answers from notes)
- similarity >= 0.55 (stricter than the 0.42 needed to enter the prompt)
- the note is at least 7 days old
- the reply does not already cite its path
- the same note was not suggested in the last 7 days
At most one suggestion per message, and the source path is always cited.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..models import Intent
from ..storage.state_db import StateDB
from .context_builder import Context, RelatedNote

logger = logging.getLogger(__name__)

SUGGEST_MIN_SCORE = 0.55
MIN_NOTE_AGE = timedelta(days=7)
COOLDOWN = timedelta(days=7)
SUGGEST_INTENTS = frozenset({Intent.CAPTURE, Intent.JOURNAL})
_META_PREFIX = "recall_suggested:"


def humanize_age(age: timedelta) -> str:
    days = age.days
    if days < 14:
        return f"{days} gün önce"
    if days < 60:
        return f"{days // 7} hafta önce"
    if days < 365:
        return f"{days // 30} ay önce"
    return f"{days // 365} yıl önce"


class RecallSuggester:
    def __init__(
        self,
        state: StateDB | None = None,
        min_score: float = SUGGEST_MIN_SCORE,
        min_age: timedelta = MIN_NOTE_AGE,
        cooldown: timedelta = COOLDOWN,
    ) -> None:
        self.state = state
        self.min_score = min_score
        self.min_age = min_age
        self.cooldown = cooldown
        self._memory: dict[str, datetime] = {}  # used when there is no StateDB

    def suggest(
        self,
        context: Context | None,
        intent: Intent | None,
        reply: str,
        now: datetime | None = None,
    ) -> str | None:
        """One "💡 Bu sana şunu hatırlatıyor" line, or None. Records the suggestion."""
        if context is None or (intent is not None and intent not in SUGGEST_INTENTS):
            return None
        now = now or datetime.now()
        for note in sorted(context.related, key=lambda n: n.score, reverse=True):
            if note.score < self.min_score:
                break
            if note.created is None or now - note.created < self.min_age:
                continue
            # Match the path, not the title: a new note titled "Uzay asansörü v2" must
            # not hide the older "Uzay asansörü" — that is exactly when the hint helps.
            if note.path in reply:
                continue
            if self._suggested_recently(note.path, now):
                continue
            self._remember(note.path, now)
            return self._format(note, now)
        return None

    @staticmethod
    def _format(note: RelatedNote, now: datetime) -> str:
        title = note.title or note.path.rsplit("/", 1)[-1].removesuffix(".md")
        age = humanize_age(now - note.created)
        return f'💡 Bu sana şunu hatırlatıyor: "{title}" — {note.path} ({age})'

    def _suggested_recently(self, path: str, now: datetime) -> bool:
        last = self._last_suggested(path)
        return last is not None and now - last < self.cooldown

    def _last_suggested(self, path: str) -> datetime | None:
        if self.state is None:
            return self._memory.get(path)
        raw = self.state.get_meta(_META_PREFIX + path)
        return datetime.fromisoformat(raw) if raw else None

    def _remember(self, path: str, now: datetime) -> None:
        if self.state is None:
            self._memory[path] = now
        else:
            self.state.set_meta(_META_PREFIX + path, now.isoformat(timespec="seconds"))
