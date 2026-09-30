"""Policy — the 'whether/when to nudge' engine (proactive layer).

Rule-based and deliberately conservative so the assistant never becomes annoying.
Rules, in order:

1. Quiet hours (`QUIET_HOURS`, default 23:00-08:00) — no message of any kind.
2. Calendar busy — wait until the current event ends. The check arrives as an
   injected `busy_until(t)` callable (CalendarTool, #11); without it the rule is off.
3. Daily rate limit (`POLICY_MAX_NUDGES_PER_DAY`, default 3) — only for `nudge`
   (assistant-initiated). A `reminder` the user asked for is never dropped by quota.

`decide()` is the single decision point; `should_notify()` / `next_window()` wrap it.
Datetimes are naive local time, like the rest of the project.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Callable

from ..config import Config
from ..storage.state_db import StateDB

logger = logging.getLogger(__name__)

KINDS = ("reminder", "nudge")
DEFAULT_QUIET_HOURS = "23:00-08:00"
DEFAULT_MAX_NUDGES_PER_DAY = 3
MAX_WINDOW_STEPS = 50
COUNT_DATE_KEY = "nudge_count_date"
COUNT_KEY = "nudge_count"

BusyUntil = Callable[[datetime], "datetime | None"]
_HHMM = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


@dataclass(frozen=True)
class QuietHours:
    start: time
    end: time

    @classmethod
    def parse(cls, value: str) -> QuietHours:
        """Parse "HH:MM-HH:MM"; the window may wrap past midnight."""
        match = _HHMM.match(value or "")
        if not match:
            raise ValueError(f"QUIET_HOURS must look like 23:00-08:00, got {value!r}")
        h1, m1, h2, m2 = (int(g) for g in match.groups())
        try:
            start, end = time(h1, m1), time(h2, m2)
        except ValueError as err:
            raise ValueError(f"QUIET_HOURS has an invalid time: {value!r}") from err
        if start == end:
            raise ValueError("QUIET_HOURS start and end must differ")
        return cls(start, end)

    def contains(self, t: datetime) -> bool:
        """Start is inclusive, end is exclusive."""
        now = t.time()
        if self.start < self.end:
            return self.start <= now < self.end
        return now >= self.start or now < self.end

    def ends_after(self, t: datetime) -> datetime:
        """The first moment after `t` at which the quiet window is over."""
        end = datetime.combine(t.date(), self.end)
        return end if end > t else end + timedelta(days=1)


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str                      # ok | quiet_hours | calendar_busy | rate_limit
    retry_at: datetime | None = None  # when not allowed: next suitable time, if any


class Policy:
    def __init__(
        self,
        state: StateDB,
        quiet_hours: str = DEFAULT_QUIET_HOURS,
        max_nudges_per_day: int = DEFAULT_MAX_NUDGES_PER_DAY,
        busy_until: BusyUntil | None = None,
    ) -> None:
        if max_nudges_per_day < 0:
            raise ValueError("max_nudges_per_day cannot be negative")
        self.state = state
        self.quiet = QuietHours.parse(quiet_hours)
        self.max_nudges_per_day = max_nudges_per_day
        self.busy_until = busy_until

    @classmethod
    def from_config(cls, state: StateDB, busy_until: BusyUntil | None = None) -> Policy:
        """Build from env; a malformed value fails loudly at startup."""
        raw_max = Config.get("POLICY_MAX_NUDGES_PER_DAY", str(DEFAULT_MAX_NUDGES_PER_DAY))
        try:
            max_nudges = int(raw_max)
        except ValueError as err:
            raise ValueError(f"POLICY_MAX_NUDGES_PER_DAY must be an integer: {raw_max!r}") from err
        return cls(
            state,
            quiet_hours=Config.get("QUIET_HOURS", DEFAULT_QUIET_HOURS),
            max_nudges_per_day=max_nudges,
            busy_until=busy_until,
        )

    # --- decisions -----------------------------------------------------------------------

    def decide(self, now: datetime | None = None, kind: str = "nudge") -> Decision:
        """Is `now` a good moment to send a message of this kind?"""
        now = now or datetime.now()
        reason = self._blocker(now, kind)
        if reason is None:
            return Decision(allowed=True, reason="ok")
        return Decision(allowed=False, reason=reason, retry_at=self.next_window(now, kind))

    def should_notify(self, ctx: dict) -> bool:
        """ctx: {"now": datetime (optional), "kind": "reminder" | "nudge" (optional)}."""
        return self.decide(ctx.get("now"), ctx.get("kind", "nudge")).allowed

    def next_window(self, now: datetime | None = None, kind: str = "nudge") -> datetime | None:
        """Earliest time >= now at which every rule allows sending; None if never."""
        t = now or datetime.now()
        self._check_kind(kind)
        if kind == "nudge" and self.max_nudges_per_day == 0:
            return None
        for _ in range(MAX_WINDOW_STEPS):
            if self.quiet.contains(t):
                t = self.quiet.ends_after(t)
                continue
            busy_end = self._busy_end(t)
            if busy_end is not None:
                t = busy_end
                continue
            if kind == "nudge" and self._count_on(t) >= self.max_nudges_per_day:
                t = datetime.combine(t.date() + timedelta(days=1), time.min)
                continue
            return t
        logger.warning("No send window found within %d steps after %s", MAX_WINDOW_STEPS, now)
        return None

    def _blocker(self, now: datetime, kind: str) -> str | None:
        self._check_kind(kind)
        if self.quiet.contains(now):
            return "quiet_hours"
        if self._busy_end(now) is not None:
            return "calendar_busy"
        if kind == "nudge" and self._count_on(now) >= self.max_nudges_per_day:
            return "rate_limit"
        return None

    def _busy_end(self, t: datetime) -> datetime | None:
        """End of the calendar event covering `t`, or None when free / unknown."""
        if self.busy_until is None:
            return None
        try:
            end = self.busy_until(t)
        except Exception:
            # A calendar outage must not hold every message back forever.
            logger.warning("Calendar check failed; treating the user as free", exc_info=True)
            return None
        return end if end is not None and end > t else None

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"invalid kind {kind!r}; expected one of {', '.join(KINDS)}")

    # --- rate-limit bookkeeping ----------------------------------------------------------

    def nudges_today(self, now: datetime | None = None) -> int:
        return self._count_on(now or datetime.now())

    def _count_on(self, t: datetime) -> int:
        if self.state.get_meta(COUNT_DATE_KEY) != t.date().isoformat():
            return 0  # the counter belongs to another day
        return int(self.state.get_meta(COUNT_KEY, "0"))

    def record_nudge(self, now: datetime | None = None, kind: str = "nudge") -> None:
        """Call after a message was actually sent. Only `nudge` counts toward the limit."""
        now = now or datetime.now()
        self._check_kind(kind)
        self.state.set_last_nudge(now)
        if kind == "nudge":
            count = self._count_on(now) + 1
            self.state.set_meta(COUNT_DATE_KEY, now.date().isoformat())
            self.state.set_meta(COUNT_KEY, str(count))
