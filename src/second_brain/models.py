"""Domain models (see Section 4 of the design document)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, date
from enum import Enum


class Intent(str, Enum):
    CAPTURE = "capture"
    QUERY = "query"
    TASK = "task"
    REMINDER = "reminder"
    JOURNAL = "journal"
    STATE = "state"
    COMMAND = "command"


@dataclass
class Note:
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    path: str | None = None
    created: datetime = field(default_factory=datetime.now)


@dataclass
class Bookmark(Note):
    url: str = ""
    summary: str = ""
    source: str | None = None        # where the link came from, e.g. instagram


@dataclass
class DayMetrics:
    mood: int | None = None          # 1-5
    energy: int | None = None        # 1-5
    productivity: int | None = None  # 1-5
    stress: int | None = None        # 1-5


@dataclass
class JournalEntry(Note):
    day: date = field(default_factory=date.today)
    metrics: DayMetrics = field(default_factory=DayMetrics)
    people: list[str] = field(default_factory=list)


@dataclass
class Reminder:
    text: str
    due: datetime
    status: str = "pending"          # pending | deferred | sent | closed
    note_path: str | None = None
    id: int | None = None            # state_db row id


ENERGY_LEVELS = ("low", "normal", "high")
CYCLE_PHASES = ("menstrual", "follicular", "ovulatory", "luteal")


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


@dataclass
class StateFlags:
    """User-reported state from State.md (vault, user-controlled). Read by Policy.

    Time-bound flags carry an end date so they expire on their own; without one
    (e.g. set by hand) they stay until changed. cycle_phase is recorded only and
    does not change any behavior.
    """
    energy: str = "normal"           # low | normal | high
    energy_until: date | None = None
    exam_week: bool = False
    exam_until: date | None = None
    cycle_phase: str | None = None   # menstrual | follicular | ovulatory | luteal
    updated: date | None = None

    def energy_on(self, day: date) -> str:
        if self.energy != "normal" and (self.energy_until is None or day <= self.energy_until):
            return self.energy
        return "normal"

    def exam_on(self, day: date) -> bool:
        return self.exam_week and (self.exam_until is None or day <= self.exam_until)

    @classmethod
    def from_frontmatter(cls, meta: dict) -> StateFlags:
        """Tolerant: hand edits with unknown values fall back to the neutral default."""
        energy = str(meta.get("energy") or "normal").strip().lower()
        phase = meta.get("cycle_phase")
        phase = str(phase).strip().lower() if phase else None
        return cls(
            energy=energy if energy in ENERGY_LEVELS else "normal",
            energy_until=_as_date(meta.get("energy_until")),
            exam_week=meta.get("exam_week") is True,
            exam_until=_as_date(meta.get("exam_until")),
            cycle_phase=phase if phase in CYCLE_PHASES else None,
            updated=_as_date(meta.get("updated")),
        )
