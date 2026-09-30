"""JournalNudger — evening "you haven't journaled today" nudge (orchestration layer).

Runs as the scheduler's `journal_check` job every 30 minutes and sends at most one
nudge per journal day, only when all hold:

- it is past JOURNAL_CHECK_TIME (default 21:00)
- today's journal is still empty
- no nudge went out for this day yet
- Policy allows a `nudge` now (quiet hours, calendar, daily limit)

If Policy says "not now", the next run tries again. Once quiet hours begin the day
is given up: "you haven't written today" is meaningless the next morning. The
message is a fixed template (no LLM); sending counts toward the daily nudge limit.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, time
from typing import Awaitable, Callable

from ..config import Config
from ..proactive.policy import Policy
from ..storage.state_db import StateDB
from ..tools.journal_writer import JournalWriter, journal_day

logger = logging.getLogger(__name__)

DEFAULT_CHECK_TIME = "21:00"
MESSAGE = "📓 Bugün günlüğüne henüz bir şey yazmadın. Günün nasıl geçti?"
_META_PREFIX = "journal_nudged:"
_HHMM = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")

Send = Callable[[str], Awaitable[None]]


def parse_check_time(value: str) -> time:
    match = _HHMM.match(value or "")
    if not match:
        raise ValueError(f"JOURNAL_CHECK_TIME must look like 21:00, got {value!r}")
    try:
        return time(int(match.group(1)), int(match.group(2)))
    except ValueError as err:
        raise ValueError(f"JOURNAL_CHECK_TIME has an invalid time: {value!r}") from err


class JournalNudger:
    def __init__(
        self,
        journal: JournalWriter,
        policy: Policy,
        state: StateDB,
        send: Send,
        check_time: time = time(21, 0),
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.journal = journal
        self.policy = policy
        self.state = state
        self.send = send
        self.check_time = check_time
        self.clock = clock

    @classmethod
    def from_config(
        cls, journal: JournalWriter, policy: Policy, state: StateDB, send: Send
    ) -> JournalNudger:
        """Read JOURNAL_CHECK_TIME; a malformed value fails loudly at startup."""
        check_time = parse_check_time(Config.get("JOURNAL_CHECK_TIME", DEFAULT_CHECK_TIME))
        return cls(journal, policy, state, send, check_time=check_time)

    async def __call__(self) -> bool:
        """One check; returns True if a nudge was sent."""
        now = self.clock()
        if now.time() < self.check_time:
            return False
        day = journal_day(now)
        key = f"{_META_PREFIX}{day.isoformat()}"
        if await asyncio.to_thread(self.state.get_meta, key):
            return False
        if await asyncio.to_thread(self.journal.has_entries, day):
            return False

        decision = await asyncio.to_thread(self.policy.decide, now, "nudge")
        if not decision.allowed:
            logger.info("Journal nudge held back (%s); will retry", decision.reason)
            return False

        try:
            await self.send(MESSAGE)
        except Exception:
            logger.exception("Sending the journal nudge failed; will retry")
            return False

        await asyncio.to_thread(self._record, now, key)
        logger.info("Journal nudge sent for %s", day)
        return True

    def _record(self, now: datetime, key: str) -> None:
        self.policy.record_nudge(now, "nudge")
        self.state.set_meta(key, now.isoformat(timespec="seconds"))
