"""CRUD for reminders (Phase 3)."""
from __future__ import annotations
from .base import Tool


class ReminderManager(Tool):
    name = "reminder_manager"

    def __init__(self, state=None) -> None:
        self.state = state

    def run(self, args: dict):
        raise NotImplementedError
