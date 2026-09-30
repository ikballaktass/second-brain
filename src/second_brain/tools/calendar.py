"""Reads Google Calendar events (Phase 2, read-only).

Two consumers:
- the LLM, via `run({"action": "list", "date": ...})` ("what's on today?")
- Policy, via `busy_until(t)`: reminders wait until the current meeting ends

Authorization is a one-time step on a machine with a browser
(`scripts/google_auth.py`), which writes the token file this tool loads.
Datetimes are converted to naive local time, like the rest of the project.
"""
from __future__ import annotations

import logging
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from ..config import Config
from .base import Tool

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
DEFAULT_TOKEN_PATH = "secrets/google_token.json"
CACHE_TTL_S = 300
MAX_EVENTS_PER_DAY = 250


@dataclass(frozen=True)
class CalendarEvent:
    start: datetime
    end: datetime
    summary: str
    all_day: bool = False
    busy: bool = True   # False for "show as free" events and invitations the user declined


def _to_local(value: dict) -> tuple[datetime, bool]:
    """Google's {"dateTime": RFC3339} or {"date": YYYY-MM-DD} -> (naive local, all_day)."""
    if "dateTime" in value:
        parsed = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed, False
    return datetime.combine(date.fromisoformat(value["date"]), time.min), True


def _parse_event(item: dict) -> CalendarEvent | None:
    if item.get("status") == "cancelled" or "start" not in item or "end" not in item:
        return None
    start, all_day = _to_local(item["start"])
    end, _ = _to_local(item["end"])
    declined = any(
        a.get("self") and a.get("responseStatus") == "declined" for a in item.get("attendees", [])
    )
    busy = item.get("transparency") != "transparent" and not declined
    return CalendarEvent(start, end, item.get("summary") or "(no title)", all_day, busy)


def _format(event: CalendarEvent) -> str:
    when = "all day" if event.all_day else f"{event.start:%H:%M}–{event.end:%H:%M}"
    return f"{when} · {event.summary}" + ("" if event.busy else " (free)")


def _day_bounds(day: date) -> tuple[str, str]:
    """RFC3339 bounds of a local day, as the API expects."""
    start = datetime.combine(day, time.min).astimezone()
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


class CalendarTool(Tool):
    name = "calendar"
    description = (
        "Read the user's Google Calendar. action=list returns the events of one day; "
        "date is YYYY-MM-DD resolved from the current time in the system prompt "
        "(omit it for today)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list"]},
            "date": {"type": "string", "description": "Day to list, YYYY-MM-DD"},
        },
        "required": ["action"],
    }

    def __init__(
        self,
        service,
        calendar_id: str = "primary",
        clock: Callable[[], float] = _time.monotonic,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.service = service
        self.calendar_id = calendar_id
        self._clock = clock
        self._today = today
        self._cache: dict[date, tuple[float, list[CalendarEvent]]] = {}
        # googleapiclient's HTTP object is not thread-safe; callers come from worker threads.
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls) -> CalendarTool:
        """Load the OAuth token and build the API client; fail loudly if it is missing."""
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token_path = Path(Config.get("GOOGLE_TOKEN_PATH", DEFAULT_TOKEN_PATH)).expanduser()
        if not token_path.is_file():
            raise RuntimeError(
                f"Google token not found at {token_path}. "
                "Run `python scripts/google_auth.py` once, or turn the calendar module off."
            )
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if not creds.valid and not creds.refresh_token:
            raise RuntimeError(
                f"Google token at {token_path} is invalid and cannot be refreshed; "
                "run `python scripts/google_auth.py` again."
            )
        # The client refreshes expired access tokens itself using the refresh token.
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return cls(service, calendar_id=Config.get("GOOGLE_CALENDAR_ID", "primary"))

    # --- reading -------------------------------------------------------------------------

    def events_on(self, day: date) -> list[CalendarEvent]:
        """Events overlapping a local day, sorted by start; cached for CACHE_TTL_S."""
        with self._lock:
            cached = self._cache.get(day)
            if cached and self._clock() - cached[0] < CACHE_TTL_S:
                return cached[1]
            time_min, time_max = _day_bounds(day)
            response = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=MAX_EVENTS_PER_DAY,
                )
                .execute()
            )
            events = [e for e in map(_parse_event, response.get("items", [])) if e]
            events.sort(key=lambda e: (e.start, e.end))
            self._cache[day] = (self._clock(), events)
            return events

    def busy_until(self, t: datetime) -> datetime | None:
        """End of the latest busy, timed event running at `t`; None when free.

        All-day and "free" events never block. API errors propagate: Policy logs
        them and treats the user as free.
        """
        ends = [
            e.end for e in self.events_on(t.date())
            if e.busy and not e.all_day and e.start <= t < e.end
        ]
        return max(ends) if ends else None

    # --- LLM-facing ----------------------------------------------------------------------

    def run(self, args: dict) -> str:
        if args.get("action") != "list":
            raise ValueError(f"unknown action {args.get('action')!r}; expected list")
        raw = args.get("date")
        if raw is None:
            day = self._today()
        else:
            try:
                day = date.fromisoformat(str(raw).strip())
            except ValueError as err:
                raise ValueError(f"'date' must be YYYY-MM-DD, got {raw!r}") from err
        try:
            events = self.events_on(day)
        except Exception as err:
            logger.warning("Calendar read failed", exc_info=True)
            raise ValueError(f"calendar is unavailable right now ({type(err).__name__})") from err
        if not events:
            return f"No events on {day.isoformat()}."
        return f"{day.isoformat()}:\n" + "\n".join(_format(e) for e in events)
