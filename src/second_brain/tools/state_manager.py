"""User-reported state (energy / exam week / cycle phase) in State.md (Phase 5).

"Bu hafta sınav haftam" or "enerjim çok düşük" become structured flags the Policy
reads to hold back assistant-initiated nudges (never the user's own reminders).
The file is written through NoteWriter, so the vault keeps its two writers, and
stays hand-editable. Values are sensitive: logs name the changed keys only.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Callable

import frontmatter

from ..models import CYCLE_PHASES, ENERGY_LEVELS, StateFlags
from .base import Tool
from .note_writer import STATE_PATH, NoteWriter

logger = logging.getLogger(__name__)

DEFAULT_ENERGY_DAYS = 3
DEFAULT_EXAM_DAYS = 7


class StateManager(Tool):
    name = "state_manager"
    description = (
        "Record the user's current state so the assistant adapts when it messages them. "
        "Use when the user reports their energy (low/normal/high), an exam period, or "
        "their cycle phase, or asks what state is recorded. action=set with only the "
        "fields they mentioned; until is an optional end date (YYYY-MM-DD) resolved from "
        "the current time in the system prompt. energy=normal or exam_week=false clears it."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "show"]},
            "energy": {"type": "string", "enum": list(ENERGY_LEVELS)},
            "exam_week": {"type": "boolean"},
            "cycle_phase": {"type": "string", "enum": [*CYCLE_PHASES, "none"]},
            "until": {"type": "string", "description": "End date YYYY-MM-DD (optional)"},
        },
        "required": ["action"],
    }

    def __init__(
        self, note_writer: NoteWriter, today: Callable[[], date] = lambda: datetime.now().date()
    ) -> None:
        self.note_writer = note_writer
        self.today = today

    def current(self) -> StateFlags:
        """Flags as stored in State.md (neutral defaults if there is no file)."""
        vault = self.note_writer.vault
        if not vault.exists(STATE_PATH):
            return StateFlags()
        return StateFlags.from_frontmatter(dict(frontmatter.loads(vault.read(STATE_PATH)).metadata))

    def run(self, args: dict) -> str:
        action = args.get("action")
        if action == "show":
            return self._show()
        if action == "set":
            return self._set(args)
        raise ValueError(f"unknown action {action!r}; expected set or show")

    def _set(self, args: dict) -> str:
        today = self.today()
        until = self._parse_until(args.get("until"), today)
        fields: dict = {}

        if "energy" in args:
            energy = args["energy"]
            if energy not in ENERGY_LEVELS:
                raise ValueError(f"'energy' must be one of {', '.join(ENERGY_LEVELS)}")
            fields["energy"] = energy
            fields["energy_until"] = (
                None if energy == "normal"
                else (until or today + timedelta(days=DEFAULT_ENERGY_DAYS)).isoformat()
            )
        if "exam_week" in args:
            exam = args["exam_week"]
            if not isinstance(exam, bool):
                raise ValueError("'exam_week' must be true or false")
            fields["exam_week"] = exam
            fields["exam_until"] = (
                (until or today + timedelta(days=DEFAULT_EXAM_DAYS)).isoformat() if exam else None
            )
        if "cycle_phase" in args:
            phase = args["cycle_phase"]
            if phase not in (*CYCLE_PHASES, "none"):
                raise ValueError(f"'cycle_phase' must be one of {', '.join(CYCLE_PHASES)} or none")
            fields["cycle_phase"] = None if phase == "none" else phase
        if not fields:
            raise ValueError("nothing to set: give energy, exam_week or cycle_phase")

        fields["updated"] = today.isoformat()
        path = self.note_writer.update_state(fields)
        logger.info("State updated: %s", ", ".join(sorted(k for k in fields if k != "updated")))
        return f"State → {path} · {self._summary(self.current(), today)}"

    def _show(self) -> str:
        today = self.today()
        return self._summary(self.current(), today)

    @staticmethod
    def _parse_until(raw, today: date) -> date | None:
        if raw is None:
            return None
        try:
            until = date.fromisoformat(str(raw).strip())
        except ValueError as err:
            raise ValueError(f"'until' must be YYYY-MM-DD, got {raw!r}") from err
        if until < today:
            raise ValueError(f"'until' ({until}) is in the past")
        return until

    @staticmethod
    def _summary(flags: StateFlags, today: date) -> str:
        parts = []
        energy = flags.energy_on(today)
        if energy != "normal":
            parts.append(f"energy: {energy}"
                         + (f" (until {flags.energy_until})" if flags.energy_until else ""))
        if flags.exam_on(today):
            parts.append("exam week" + (f" until {flags.exam_until}" if flags.exam_until else ""))
        if flags.cycle_phase:
            parts.append(f"cycle: {flags.cycle_phase}")
        return " · ".join(parts) if parts else "No active state flags."
