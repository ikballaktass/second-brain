"""EnergySuggester — "your energy is high, good time for X" (orchestration layer).

Scheduler job `energy_suggestion`, every 30 minutes. At most one suggestion per day,
between 10:00 and 22:00, only while State.md says `energy: high` and Policy allows a
`nudge` (it counts toward the daily limit). Fixed template, no LLM.

A note qualifies as the topic only on evidence found in the vault itself:

- effort     — frontmatter `effort`/`difficulty: high`, a `zor`/`efor` tag, or ≥ 5 open tasks
- near_done  — ≥ 3 checkbox tasks, ≥ 60 % checked, at least one still open
- frequent   — ≥ 3 other notes/journal entries from the last 14 days closely similar
               (≥ 0.55) to it; needs the index (skipped when recall is off)

Candidates: notes edited in the last 30 days, except README.md, State.md, journal/ and
bookmarks/. More criteria met wins; ties prefer effort (fits high energy), then
near_done, then frequent, then the most recently edited. A note suggested in the last
7 days is skipped. No qualifying note → no message that day.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Awaitable, Callable

import frontmatter

from ..config import Config
from ..models import StateFlags
from ..proactive.policy import Policy
from ..storage.index_store import IndexStore, title_from_path
from ..storage.state_db import StateDB
from ..storage.vault_repository import VaultRepository

logger = logging.getLogger(__name__)

WINDOW_START = time(10, 0)
WINDOW_END = time(22, 0)
CANDIDATE_DAYS = 30
FREQUENT_DAYS = 14
FREQUENT_MIN_NOTES = 3
FREQUENT_MIN_SCORE = 0.55
NEAR_DONE_MIN_TASKS = 3
NEAR_DONE_RATIO = 0.6
EFFORT_MIN_OPEN_TASKS = 5
COOLDOWN = timedelta(days=7)
EXCLUDED_FILES = {"README.md", "State.md"}
EXCLUDED_DIRS = ("journal/", "bookmarks/")
EFFORT_TAGS = {"zor", "efor"}
HIGH_VALUES = {"high", "yüksek"}
REASON_ORDER = ("effort", "near_done", "frequent")

_TASK = re.compile(r"^\s*[-*+]\s+\[([ xX])\]", re.MULTILINE)
_INLINE_TAG = re.compile(r"(?<![\w#])#([\wçğıöşüÇĞİÖŞÜ/-]+)")
_DAY_KEY = "energy_suggestion:"
_NOTE_KEY = "energy_suggested:"

Send = Callable[[str], Awaitable[None]]
FlagsReader = Callable[[], StateFlags]


@dataclass
class Topic:
    path: str
    title: str
    modified: datetime
    reasons: set[str] = field(default_factory=set)
    done_ratio: float | None = None

    def rank(self) -> tuple:
        return (len(self.reasons), *(r in self.reasons for r in REASON_ORDER), self.modified)


def task_counts(body: str) -> tuple[int, int]:
    """(done, total) Obsidian checkbox tasks in a note body."""
    marks = _TASK.findall(body)
    return sum(m in "xX" for m in marks), len(marks)


def _tags(meta: dict, body: str) -> set[str]:
    raw = meta.get("tags") or []
    tags = {str(t).lower().lstrip("#") for t in (raw if isinstance(raw, list) else [raw])}
    tags |= {t.lower() for t in _INLINE_TAG.findall(body)}
    return tags


def is_high_effort(meta: dict, body: str) -> bool:
    for key in ("effort", "difficulty"):
        if str(meta.get(key, "")).strip().lower() in HIGH_VALUES:
            return True
    if _tags(meta, body) & EFFORT_TAGS:
        return True
    done, total = task_counts(body)
    return total - done >= EFFORT_MIN_OPEN_TASKS


def near_done_ratio(body: str) -> float | None:
    """Completion ratio if the note is near done, else None."""
    done, total = task_counts(body)
    if total >= NEAR_DONE_MIN_TASKS and done < total and done / total >= NEAR_DONE_RATIO:
        return done / total
    return None


class EnergySuggester:
    def __init__(
        self,
        vault: VaultRepository,
        index: IndexStore | None,
        policy: Policy,
        state: StateDB,
        flags: FlagsReader,
        send: Send,
        user_name: str | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.vault = vault
        self.index = index
        self.policy = policy
        self.state = state
        self.flags = flags
        self.send = send
        self.user_name = (user_name or "").strip() or None
        self.clock = clock

    @classmethod
    def from_config(cls, vault, index, policy, state, flags, send) -> EnergySuggester:
        return cls(vault, index, policy, state, flags, send, user_name=Config.get("USER_NAME"))

    # --- job -----------------------------------------------------------------------------

    async def __call__(self) -> bool:
        """One check; returns True if a suggestion was sent."""
        now = self.clock()
        if not WINDOW_START <= now.time() < WINDOW_END:
            return False
        if await asyncio.to_thread(self._energy, now) != "high":
            return False
        day_key = _DAY_KEY + now.date().isoformat()
        if await asyncio.to_thread(self.state.get_meta, day_key):
            return False
        decision = await asyncio.to_thread(self.policy.decide, now, "nudge")
        if not decision.allowed:
            logger.info("Energy suggestion held back (%s); will retry", decision.reason)
            return False

        topic = await asyncio.to_thread(self.pick_topic, now)
        if topic is None:
            logger.info("Energy is high but no note qualifies as a topic today")
            return False
        try:
            await self.send(self.message(topic))
        except Exception:
            logger.exception("Sending the energy suggestion failed; will retry")
            return False
        await asyncio.to_thread(self._record, now, day_key, topic)
        return True

    def _energy(self, now: datetime) -> str:
        try:
            return self.flags().energy_on(now.date())
        except Exception:
            logger.warning("Could not read state flags", exc_info=True)
            return "normal"

    def _record(self, now: datetime, day_key: str, topic: Topic) -> None:
        stamp = now.isoformat(timespec="seconds")
        self.policy.record_nudge(now, "nudge")
        self.state.set_meta(day_key, stamp)
        self.state.set_meta(_NOTE_KEY + topic.path, stamp)

    # --- topic ---------------------------------------------------------------------------

    def pick_topic(self, now: datetime) -> Topic | None:
        """Best qualifying note, or None."""
        topics = []
        for path, modified in self._candidates(now):
            if self._suggested_recently(path, now):
                continue
            topic = self._assess(path, modified, now)
            if topic is not None and topic.reasons:
                topics.append(topic)
        return max(topics, key=Topic.rank) if topics else None

    def _candidates(self, now: datetime) -> list[tuple[str, datetime]]:
        cutoff = now - timedelta(days=CANDIDATE_DAYS)
        found = []
        for path in self.vault.list():
            if path in EXCLUDED_FILES or path.startswith(EXCLUDED_DIRS):
                continue
            modified = self.vault.modified_at(path)
            if modified >= cutoff:
                found.append((path, modified))
        return found

    def _assess(self, path: str, modified: datetime, now: datetime) -> Topic | None:
        try:
            post = frontmatter.loads(self.vault.read(path))
        except Exception:
            logger.warning("Skipping unreadable note %s", path, exc_info=True)
            return None
        meta, body = dict(post.metadata), post.content
        topic = Topic(path, title_from_path(path), modified)
        if is_high_effort(meta, body):
            topic.reasons.add("effort")
        ratio = near_done_ratio(body)
        if ratio is not None:
            topic.reasons.add("near_done")
            topic.done_ratio = ratio
        if self._is_frequent(path, body, now):
            topic.reasons.add("frequent")
        return topic

    def _is_frequent(self, path: str, body: str, now: datetime) -> bool:
        if self.index is None:
            return False
        query = f"{title_from_path(path)}\n{body[:500]}"
        try:
            hits = self.index.search(query, k=20)
        except Exception:
            logger.warning("Index search failed; skipping the frequency check", exc_info=True)
            return False
        cutoff = now - timedelta(days=FREQUENT_DAYS)
        related = 0
        for hit in hits:
            if hit.path == path or hit.score < FREQUENT_MIN_SCORE:
                continue
            try:
                if self.vault.modified_at(hit.path) >= cutoff:
                    related += 1
            except FileNotFoundError:
                continue
        return related >= FREQUENT_MIN_NOTES

    def _suggested_recently(self, path: str, now: datetime) -> bool:
        raw = self.state.get_meta(_NOTE_KEY + path)
        return raw is not None and now - datetime.fromisoformat(raw) < COOLDOWN

    # --- message -------------------------------------------------------------------------

    def message(self, topic: Topic) -> str:
        who = f"{self.user_name}, " if self.user_name else ""
        title = f'"{topic.title}"'
        if "effort" in topic.reasons:
            text = (f"{title} çok efor isteyen bir konu. "
                    "Enerjin yüksekken ona girişmek için iyi bir zaman olabilir.")
        elif "near_done" in topic.reasons:
            pct = round((topic.done_ratio or 0) * 100)
            text = (f"{title} bitmeye çok yakın (%{pct}). "
                    "Enerjin yüksekken bitirmek için iyi bir zaman olabilir.")
        else:
            text = (f"son günlerde sık sık {title} üzerinde çalışıyordun. "
                    "Enerjin yüksekken buna bakmak için iyi bir zaman olabilir.")
        if not who:
            text = text[0].upper() + text[1:]
        return f"⚡ {who}{text} ({topic.path})"
