"""ReminderDispatcher — the proactive send path for due reminders (orchestration layer).

Wired as the Scheduler's `on_due` handler:

    due reminders ─▶ Policy.decide(now, "reminder")
        ├─ allowed ─▶ send one message ─▶ mark each `sent` ─▶ Policy.record_nudge
        └─ not now ─▶ defer each to Policy's retry_at (fallback: +30 min)

Every reminder handed in leaves the due set (sent or deferred), which is the
Scheduler's contract. Sending arrives as an injected `send` coroutine because
the interface layer sits above this one.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from ..models import Reminder
from ..proactive.policy import Policy
from ..tools.reminder_manager import ReminderManager

logger = logging.getLogger(__name__)

NO_WINDOW_RETRY = timedelta(minutes=30)
SEND_FAILURE_RETRY = timedelta(minutes=5)

Send = Callable[[str], Awaitable[None]]


def compose(reminders: list[Reminder]) -> str:
    """Fixed template, no LLM: the user's own words, delivered as written."""
    if len(reminders) == 1:
        return f"⏰ Hatırlatma: {reminders[0].text}"
    lines = "\n".join(f"• {r.text}" for r in reminders)
    return f"⏰ Hatırlatmalar:\n{lines}"


class ReminderDispatcher:
    def __init__(
        self,
        reminders: ReminderManager,
        policy: Policy,
        send: Send,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.reminders = reminders
        self.policy = policy
        self.send = send
        self.clock = clock

    async def __call__(self, due: list[Reminder]) -> None:
        if not due:
            return
        now = self.clock()
        decision = await asyncio.to_thread(self.policy.decide, now, "reminder")

        if not decision.allowed:
            until = decision.retry_at or now + NO_WINDOW_RETRY
            await asyncio.to_thread(self._defer_all, due, until)
            logger.info(
                "Deferred %d reminder(s) until %s (%s)", len(due), until, decision.reason
            )
            return

        try:
            await self.send(compose(due))
        except Exception:
            until = now + SEND_FAILURE_RETRY
            logger.exception("Sending reminders failed; retrying at %s", until)
            await asyncio.to_thread(self._defer_all, due, until)
            return

        await asyncio.to_thread(self._mark_sent, due, now)
        logger.info("Sent %d reminder(s)", len(due))

    def _defer_all(self, due: list[Reminder], until: datetime) -> None:
        for reminder in due:
            self._safely(self.reminders.defer, reminder.id, until)

    def _mark_sent(self, due: list[Reminder], now: datetime) -> None:
        for reminder in due:
            self._safely(self.reminders.mark_sent, reminder.id)
        self.policy.record_nudge(now, "reminder")

    @staticmethod
    def _safely(transition, reminder_id: int, *args) -> None:
        """One bad row (e.g. closed by the user mid-tick) must not block the others."""
        try:
            transition(reminder_id, *args)
        except ValueError as err:
            logger.warning("Skipped reminder #%s: %s", reminder_id, err)
