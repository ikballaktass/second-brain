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


@dataclass
class StateFlag:
    type: str                        # energy | cycle | exam
    value: str
    period: str | None = None
