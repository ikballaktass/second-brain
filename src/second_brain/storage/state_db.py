"""State DB — SQLite, OPERATIONAL state only (storage layer).

Reminder queue, conversation checkpoints, scheduler jobs, last-nudge timestamps.
NOT user content — that lives in the vault.
"""
from __future__ import annotations


class StateDB:
    def __init__(self, path: str = "state.db") -> None:
        self.path = path
        # TODO: connect + create tables

    def reminders_due(self) -> list:
        raise NotImplementedError

    def save_message(self, chat_id: str, role: str, text: str) -> None:
        raise NotImplementedError

    def jobs(self) -> list:
        raise NotImplementedError
