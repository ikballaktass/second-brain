"""Reminder CRUD + lifecycle, persisted in StateDB (Phase 3).

The LLM-facing `run()` offers create / list / close. The lifecycle methods
(`due`, `mark_sent`, `defer`, `close`) are for the proactive path (#16).
StateDB only stores rows; which status transitions are legal is decided here:

    pending ──▶ sent ──▶ closed
       │  ▲
       ▼  │
     deferred            (pending/deferred may also be closed directly = cancel)
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..models import Reminder
from ..storage.state_db import StateDB
from .base import Tool

PAST_TOLERANCE = timedelta(minutes=1)
MAX_CANDIDATES_SHOWN = 5

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"sent", "deferred", "closed"}),
    "deferred": frozenset({"sent", "deferred", "closed"}),
    "sent": frozenset({"closed"}),
    "closed": frozenset(),
}


def _parse_due(value) -> datetime:
    """Parse an ISO datetime from the LLM into naive local time."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("'due' must be an ISO datetime string, e.g. 2026-10-01T10:00")
    try:
        due = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as err:
        raise ValueError(f"'due' is not an ISO datetime: {value!r}") from err
    if due.tzinfo is not None:
        due = due.astimezone().replace(tzinfo=None)
    return due


def _fold(text: str) -> str:
    """Case-insensitive form that also matches Turkish 'İ' against 'i'."""
    return text.casefold().replace("̇", "")


def _format(reminder: Reminder) -> str:
    when = reminder.due.strftime("%Y-%m-%d %H:%M")
    return f"#{reminder.id} · {when} · {reminder.text} ({reminder.status})"


class ReminderManager(Tool):
    name = "reminder_manager"
    description = (
        "Manage the user's timed reminders. action=create needs text and due (ISO local "
        "datetime, e.g. 2026-10-01T10:00, resolved from the current time given in the "
        "system prompt). action=list shows open reminders. action=close cancels or "
        "completes one, by id or by a query matching its text."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "close"]},
            "text": {"type": "string", "description": "What to remind the user about"},
            "due": {"type": "string", "description": "ISO local datetime, e.g. 2026-10-01T10:00"},
            "id": {"type": "integer", "description": "Reminder id, for close"},
            "query": {
                "type": "string",
                "description": "Words from the reminder's text, for close when id is unknown",
            },
        },
        "required": ["action"],
    }

    def __init__(self, state: StateDB) -> None:
        self.state = state

    def run(self, args: dict) -> str:
        action = args.get("action")
        if action == "create":
            return self._create(args)
        if action == "list":
            return self._list()
        if action == "close":
            return self._close(args)
        raise ValueError(f"unknown action {action!r}; expected create, list or close")

    # --- LLM-facing actions --------------------------------------------------------------

    def _create(self, args: dict) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("'text' is required to create a reminder")
        reminder = self.create(text, _parse_due(args.get("due")))
        return f"Reminder set → {_format(reminder)}"

    def _list(self) -> str:
        reminders = self.open_reminders()
        if not reminders:
            return "No open reminders."
        return "\n".join(_format(r) for r in reminders)

    def _close(self, args: dict) -> str:
        reminder_id = args.get("id")
        if reminder_id is None:
            reminder_id = self._find_by_query(args.get("query")).id
        elif not isinstance(reminder_id, int) or isinstance(reminder_id, bool):
            raise ValueError("'id' must be an integer")
        reminder = self.close(reminder_id)
        return f"Reminder closed → {_format(reminder)}"

    def _find_by_query(self, query) -> Reminder:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("give an 'id' or a 'query' to close a reminder")
        needle = _fold(query.strip())
        matches = [r for r in self.open_reminders() if needle in _fold(r.text)]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError(f"no open reminder matches {query!r}")
        shown = "; ".join(_format(r) for r in matches[:MAX_CANDIDATES_SHOWN])
        raise ValueError(f"{len(matches)} reminders match {query!r}, give an id: {shown}")

    # --- Python API (CRUD + lifecycle) ---------------------------------------------------

    def create(
        self, text: str, due: datetime, note_path: str | None = None, now: datetime | None = None
    ) -> Reminder:
        """Queue a pending reminder; `due` must not be in the past."""
        if due < (now or datetime.now()) - PAST_TOLERANCE:
            raise ValueError(f"due time {due:%Y-%m-%d %H:%M} is in the past")
        reminder_id = self.state.add_reminder(text, due, note_path)
        return self.state.get_reminder(reminder_id)

    def open_reminders(self) -> list[Reminder]:
        """Every reminder that is not closed, ordered by due time."""
        return [r for r in self.state.list_reminders() if r.status != "closed"]

    def due(self, now: datetime | None = None) -> list[Reminder]:
        """Pending or deferred reminders whose time has come."""
        return self.state.reminders_due(now)

    def mark_sent(self, reminder_id: int) -> Reminder:
        return self._transition(reminder_id, "sent")

    def defer(self, reminder_id: int, until: datetime) -> Reminder:
        """Policy said 'not now': try again at `until`."""
        self._check_transition(self._get(reminder_id), "deferred")
        self.state.defer_reminder(reminder_id, until)
        return self._get(reminder_id)

    def close(self, reminder_id: int) -> Reminder:
        return self._transition(reminder_id, "closed")

    def _transition(self, reminder_id: int, status: str) -> Reminder:
        self._check_transition(self._get(reminder_id), status)
        self.state.set_reminder_status(reminder_id, status)
        return self._get(reminder_id)

    def _get(self, reminder_id: int) -> Reminder:
        reminder = self.state.get_reminder(reminder_id)
        if reminder is None:
            raise ValueError(f"reminder #{reminder_id} not found")
        return reminder

    @staticmethod
    def _check_transition(reminder: Reminder, status: str) -> None:
        if status not in ALLOWED_TRANSITIONS[reminder.status]:
            raise ValueError(
                f"reminder #{reminder.id} is {reminder.status}; cannot become {status}"
            )
