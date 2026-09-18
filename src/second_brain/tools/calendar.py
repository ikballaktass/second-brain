"""Reads/writes Google Calendar events (Phase 2)."""
from __future__ import annotations
from .base import Tool


class CalendarTool(Tool):
    name = "calendar"

    def __init__(self) -> None:
        ...

    def run(self, args: dict):
        raise NotImplementedError
