"""Scheduler — background loop that drives proactive behavior (proactive layer).

Runs on APScheduler's AsyncIOScheduler inside the bot's own asyncio loop (the
gateway starts it from Application.post_init). Every `interval_s` seconds,
`tick()` looks up due reminders and hands them to `on_due`.

`on_due` contract (wired in #16): it decides with Policy whether to send now, and
MUST move every reminder it receives out of the due set — `sent` or `deferred`
to a later time. A reminder left pending is handed over again on the next tick.

This layer never imports orchestration or interface; whatever it needs from
above (sending, composing) arrives as the injected `on_due` callable.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from ..models import Reminder
from ..storage.state_db import StateDB

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 60
REMINDER_JOB = "reminder"

DueHandler = Callable[[list[Reminder]], Awaitable[None]]
JobFunc = Callable[[], Awaitable[object]]


class Scheduler:
    def __init__(
        self,
        state: StateDB,
        on_due: DueHandler | None = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        scheduler: AsyncIOScheduler | None = None,
    ) -> None:
        if interval_s <= 0:
            raise ValueError("interval_s must be positive")
        self.state = state
        self.on_due = on_due
        self.interval_s = interval_s
        self._scheduler = scheduler or AsyncIOScheduler()

    @property
    def running(self) -> bool:
        return self._scheduler.running

    async def tick(self, now: datetime | None = None) -> list[Reminder]:
        """One pass: fetch due reminders and hand them to `on_due`. Never raises."""
        try:
            due = await asyncio.to_thread(self.state.reminders_due, now)
        except Exception:
            logger.exception("Could not read due reminders")
            return []
        if not due:
            return []
        if self.on_due is None:
            logger.info("%d reminder(s) due, but no handler is wired yet", len(due))
            return due
        try:
            await self.on_due(due)
        except Exception:
            logger.exception("Due-reminder handler failed; they will be retried next tick")
        return due

    def add_job(self, kind: str, func: JobFunc, trigger, run_now: bool = False) -> None:
        """Schedule `func` as the single job of this kind and record it in StateDB.

        max_instances=1: a slow run is never overlapped by the next one.
        coalesce=True: runs missed while busy/asleep collapse into one.
        """
        self.state.upsert_job(kind, str(trigger))  # validates kind before scheduling

        async def run_and_record() -> None:
            try:
                await func()
            except Exception:
                logger.exception("Job %s failed", kind)
            finally:
                try:
                    await asyncio.to_thread(self.state.mark_job_run, kind)
                except Exception:
                    logger.exception("Could not record last run of job %s", kind)

        options = {"next_run_time": datetime.now()} if run_now else {}
        self._scheduler.add_job(
            run_and_record,
            trigger,
            id=kind,
            name=kind,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            **options,
        )

    def start(self) -> None:
        """Register the reminder tick and start. Call from inside the running loop."""
        if self.running:
            return
        # run_now: reminders that fell due while the bot was down go out right away.
        self.add_job(
            REMINDER_JOB, self.tick, IntervalTrigger(seconds=self.interval_s), run_now=True
        )
        self._scheduler.start()
        logger.info("Scheduler started (reminder tick every %ss)", self.interval_s)

    def shutdown(self) -> None:
        if self.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
