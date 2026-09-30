"""Journal analyzer — end-of-day metric extraction (tools layer).

Fills a FIXED metric schema (mood/energy/productivity/stress, 1-5); it must NOT
invent new metrics. Results go into the day note's frontmatter as suggestions the
user can correct, through JournalWriter (the vault keeps its two writers).

Not a Tool: the scheduler drives it (job `analyze`, daily at 04:30 right after the
04:00 day boundary, and once at startup). Each run looks back over the last 7
finished days, so days missed while the bot was down are caught up.

Rules:
- only metrics that are still empty are written — a value the user set by hand, or
  an earlier run's, is never overwritten
- a day is analyzed once (`journal_analyzed:<date>` in state_db), even if some
  metrics came back null; an LLM failure leaves it unmarked so the next run retries
- an empty or missing journal never costs an LLM call
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, fields
from datetime import date, datetime, timedelta
from typing import Callable

from ..llm_client import LLMError
from ..models import DayMetrics
from ..prompts import JOURNAL_METRICS_SYSTEM_PROMPT
from ..storage.state_db import StateDB
from .journal_writer import JournalWriter, journal_day

logger = logging.getLogger(__name__)

METRICS = tuple(f.name for f in fields(DayMetrics))   # the fixed schema, nothing else
LOOKBACK_DAYS = 7
METRICS_MAX_TOKENS = 100
_META_PREFIX = "journal_analyzed:"


def parse_metrics(text: str) -> DayMetrics:
    """Read the four metrics from the LLM reply; anything invalid or unknown is dropped."""
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    data = None
    if start != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        return DayMetrics()
    values = {}
    for name in METRICS:  # extra keys the model invents are simply never read
        value = data.get(name)
        valid = isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5
        values[name] = value if valid else None
    return DayMetrics(**values)


class JournalAnalyzer:
    def __init__(
        self,
        llm,
        journal: JournalWriter,
        state: StateDB,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.llm = llm
        self.journal = journal
        self.state = state
        self.clock = clock

    def analyze(self, day: date) -> DayMetrics:
        """Rate one day and fill its empty metric fields; returns what the LLM gave.

        Raises LLMError if the model call fails (the caller decides about retrying).
        """
        found = self.journal.read_day(day)
        if found is None or not found[1]:
            return DayMetrics()
        frontmatter, body = found

        # Page content is the user's own, but keep it clearly framed as data.
        safe_body = body.replace("</journal>", "")
        prompt = f'<journal date="{day.isoformat()}">\n{safe_body}\n</journal>'
        result = self.llm.complete(
            prompt, system=JOURNAL_METRICS_SYSTEM_PROMPT, max_tokens=METRICS_MAX_TOKENS
        )
        metrics = parse_metrics(result.text)

        missing = {
            name: value for name, value in asdict(metrics).items()
            if value is not None and frontmatter.get(name) is None
        }
        if missing:
            self.journal.annotate(day, missing)
        return metrics

    def pending_days(self, now: datetime | None = None) -> list[date]:
        """Finished journal days in the lookback window that were not analyzed yet."""
        today = journal_day(now or self.clock())
        days = [today - timedelta(days=n) for n in range(LOOKBACK_DAYS, 0, -1)]
        return [
            d for d in days
            if not self.state.get_meta(_META_PREFIX + d.isoformat())
            and self.journal.has_entries(d)
        ]

    def run_pending(self, now: datetime | None = None) -> list[date]:
        """Analyze every pending day; return the days that were completed."""
        done = []
        for day in self.pending_days(now):
            try:
                metrics = self.analyze(day)
            except LLMError as err:
                logger.warning("Journal analysis for %s failed (%s); will retry", day, err)
                continue
            except Exception:
                logger.exception("Journal analysis for %s failed; will retry", day)
                continue
            stamp = (now or self.clock()).isoformat(timespec="seconds")
            self.state.set_meta(_META_PREFIX + day.isoformat(), stamp)
            logger.info("Analyzed journal %s: %s", day, asdict(metrics))
            done.append(day)
        return done

    async def __call__(self) -> None:
        """Scheduler job entry point (runs the blocking work off the event loop)."""
        await asyncio.to_thread(self.run_pending)
