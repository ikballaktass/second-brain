"""State DB — SQLite, OPERATIONAL state only (storage layer).

Reminder queue, conversation history, scheduler jobs, last-nudge timestamps.
NOT user content — that lives in the vault. Losing this file loses no notes.

Datetimes follow the rest of the project: naive local time. They are stored as
ISO text with second precision, so a fixed-width string compare in SQL is a
correct time compare. Timezone-aware datetimes are rejected to avoid silently
mixing the two.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..models import Reminder

SCHEMA_VERSION = 1
REMINDER_STATUSES = ("pending", "deferred", "sent", "closed")
ACTIVE_STATUSES = ("pending", "deferred")
MESSAGE_ROLES = ("user", "assistant")
JOB_KINDS = ("reminder", "journal_check", "morning_brief", "analyze", "energy_suggestion")
LAST_NUDGE_KEY = "last_nudge_ts"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  due  TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ({", ".join(f"'{s}'" for s in REMINDER_STATUSES)})),
  note_path TEXT,
  created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminders_status_due ON reminders (status, due);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  chat_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ({", ".join(f"'{r}'" for r in MESSAGE_ROLES)})),
  text TEXT NOT NULL,
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages (chat_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages (ts);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL UNIQUE,
  schedule TEXT,
  last_run TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""


@dataclass
class StoredMessage:
    id: int
    chat_id: str
    role: str
    text: str
    ts: datetime


@dataclass
class Job:
    id: int
    kind: str
    schedule: str | None
    last_run: datetime | None


def _iso(value: datetime) -> str:
    """Serialize a naive datetime; reject anything else."""
    if not isinstance(value, datetime):
        raise ValueError(f"expected a datetime, got {type(value).__name__}")
    if value.tzinfo is not None and value.utcoffset() is not None:
        raise ValueError("timezone-aware datetimes are not supported; use naive local time")
    return value.isoformat(timespec="seconds")


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _require_choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ValueError(f"invalid {field} {value!r}; expected one of {', '.join(allowed)}")
    return value


def _to_reminder(row: sqlite3.Row) -> Reminder:
    return Reminder(
        id=row["id"],
        text=row["text"],
        due=datetime.fromisoformat(row["due"]),
        status=row["status"],
        note_path=row["note_path"],
    )


class StateDB:
    def __init__(self, path: str = "state.db") -> None:
        self.path = path
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        # One connection shared across threads (bot handler via asyncio.to_thread,
        # scheduler); the lock serializes every statement on it.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Run one statement in its own transaction."""
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> StateDB:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- reminders -----------------------------------------------------------------------

    def add_reminder(self, text: str, due: datetime, note_path: str | None = None) -> int:
        """Queue a pending reminder and return its id."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("reminder text is empty")
        cur = self._execute(
            "INSERT INTO reminders (text, due, status, note_path, created) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (text.strip(), _iso(due), note_path, _iso(datetime.now())),
        )
        return cur.lastrowid

    def get_reminder(self, reminder_id: int) -> Reminder | None:
        rows = self._query("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        return _to_reminder(rows[0]) if rows else None

    def list_reminders(self, status: str | None = None) -> list[Reminder]:
        """All reminders ordered by due time, optionally filtered by status."""
        if status is None:
            rows = self._query("SELECT * FROM reminders ORDER BY due, id")
        else:
            _require_choice(status, REMINDER_STATUSES, "status")
            rows = self._query(
                "SELECT * FROM reminders WHERE status = ? ORDER BY due, id", (status,)
            )
        return [_to_reminder(r) for r in rows]

    def reminders_due(self, now: datetime | None = None) -> list[Reminder]:
        """Pending or deferred reminders whose due time is at or before `now`."""
        rows = self._query(
            "SELECT * FROM reminders WHERE status IN (?, ?) AND due <= ? ORDER BY due, id",
            (*ACTIVE_STATUSES, _iso(now or datetime.now())),
        )
        return [_to_reminder(r) for r in rows]

    def set_reminder_status(self, reminder_id: int, status: str) -> None:
        _require_choice(status, REMINDER_STATUSES, "status")
        cur = self._execute(
            "UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id)
        )
        if cur.rowcount == 0:
            raise KeyError(f"reminder {reminder_id} not found")

    def defer_reminder(self, reminder_id: int, until: datetime) -> None:
        """Mark a reminder deferred and move its due time to the next window."""
        cur = self._execute(
            "UPDATE reminders SET status = 'deferred', due = ? WHERE id = ?",
            (_iso(until), reminder_id),
        )
        if cur.rowcount == 0:
            raise KeyError(f"reminder {reminder_id} not found")

    def delete_reminder(self, reminder_id: int) -> None:
        cur = self._execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        if cur.rowcount == 0:
            raise KeyError(f"reminder {reminder_id} not found")

    # --- messages ------------------------------------------------------------------------

    def save_message(self, chat_id: int | str, role: str, text: str) -> None:
        _require_choice(role, MESSAGE_ROLES, "role")
        if not isinstance(text, str):
            raise ValueError("message text must be a string")
        self._execute(
            "INSERT INTO messages (chat_id, role, text, ts) VALUES (?, ?, ?, ?)",
            (str(chat_id), role, text, _iso(datetime.now())),
        )

    def recent_messages(self, chat_id: int | str, limit: int = 20) -> list[StoredMessage]:
        """The last `limit` messages of a chat, oldest first."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        rows = self._query(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (str(chat_id), limit),
        )
        return [
            StoredMessage(
                id=r["id"], chat_id=r["chat_id"], role=r["role"], text=r["text"],
                ts=datetime.fromisoformat(r["ts"]),
            )
            for r in reversed(rows)
        ]

    def prune_messages(self, older_than: datetime) -> int:
        """Delete messages older than the cutoff; return how many were removed."""
        cur = self._execute("DELETE FROM messages WHERE ts < ?", (_iso(older_than),))
        return cur.rowcount

    # --- jobs ----------------------------------------------------------------------------

    def jobs(self) -> list[Job]:
        rows = self._query("SELECT * FROM jobs ORDER BY id")
        return [
            Job(id=r["id"], kind=r["kind"], schedule=r["schedule"], last_run=_parse(r["last_run"]))
            for r in rows
        ]

    def upsert_job(self, kind: str, schedule: str | None) -> None:
        """Create the job of this kind or update its schedule (one job per kind)."""
        _require_choice(kind, JOB_KINDS, "job kind")
        self._execute(
            "INSERT INTO jobs (kind, schedule) VALUES (?, ?) "
            "ON CONFLICT(kind) DO UPDATE SET schedule = excluded.schedule",
            (kind, schedule),
        )

    def mark_job_run(self, kind: str, when: datetime | None = None) -> None:
        _require_choice(kind, JOB_KINDS, "job kind")
        cur = self._execute(
            "UPDATE jobs SET last_run = ? WHERE kind = ?", (_iso(when or datetime.now()), kind)
        )
        if cur.rowcount == 0:
            raise KeyError(f"job {kind!r} not found")

    # --- meta ----------------------------------------------------------------------------

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        rows = self._query("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_meta(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def last_nudge(self) -> datetime | None:
        return _parse(self.get_meta(LAST_NUDGE_KEY))

    def set_last_nudge(self, when: datetime | None = None) -> None:
        self.set_meta(LAST_NUDGE_KEY, _iso(when or datetime.now()))
