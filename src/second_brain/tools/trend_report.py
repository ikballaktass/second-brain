"""Descriptive weekly/monthly trends of the journal metrics, on request (Phase 5).

Backward-looking only: averages, lowest/highest day, and the change against the
previous period of the same length. It never forecasts or explains causes. A metric
with too few days of data gets "yetersiz veri" instead of an average, and the
comparison is shown only when the previous period also has enough data.

Reads the journal frontmatter (JournalAnalyzer's metrics plus any hand edits); no
LLM call. The report is Turkish because it is shown to the user as is.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from .base import Tool
from .journal_analyzer import METRICS
from .journal_writer import JournalWriter, journal_day

PERIOD_DAYS = {"week": 7, "month": 30}
MIN_DAYS = {"week": 3, "month": 7}
STABLE_DELTA = 0.3
NO_FORECAST = "Bu özet sadece geçmişi anlatır; tahmin içermez."
LABELS = {"mood": "ruh hali", "energy": "enerji", "productivity": "üretkenlik", "stress": "stres"}
_DAYS = ("Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz")
_MONTHS = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")


def _day(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]}"


def _num(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def _valid(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5


@dataclass(frozen=True)
class MetricStats:
    days: int
    average: float | None = None
    low: tuple[int, date] | None = None
    high: tuple[int, date] | None = None


class TrendReport(Tool):
    name = "trend_report"
    description = (
        "Summarize how the user's journal metrics (mood, energy, productivity, stress) went "
        "over the last week or month, compared with the period before. Backward-looking "
        "only. Use when the user asks how their week/month went or about their mood, "
        "energy or stress trends."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "period": {"type": "string", "enum": list(PERIOD_DAYS)},
            "metrics": {
                "type": "array",
                "items": {"type": "string", "enum": list(METRICS)},
                "description": "Only these metrics (default: all four)",
            },
        },
        "required": ["period"],
    }

    def __init__(
        self, journal: JournalWriter, clock: Callable[[], datetime] = datetime.now
    ) -> None:
        self.journal = journal
        self.clock = clock

    def run(self, args: dict) -> str:
        period = args.get("period")
        if period not in PERIOD_DAYS:
            raise ValueError("'period' must be week or month")
        metrics = args.get("metrics") or list(METRICS)
        if not isinstance(metrics, list) or any(m not in METRICS for m in metrics):
            raise ValueError(f"'metrics' must be a list drawn from {', '.join(METRICS)}")
        return self.report(period, [m for m in METRICS if m in metrics])

    def report(self, period: str, metrics: list[str]) -> str:
        length = PERIOD_DAYS[period]
        end = journal_day(self.clock())
        current = [end - timedelta(days=n) for n in range(length - 1, -1, -1)]
        previous = [d - timedelta(days=length) for d in current]

        entries = self._read(current)
        header = f"📊 Son {length} gün ({_day(current[0])} – {_day(current[-1])})"
        if not entries:
            return f"{header} için henüz günlük kaydı yok.\n{NO_FORECAST}"

        with_metrics = sum(
            any(_valid(meta.get(m)) for m in METRICS) for meta in entries.values()
        )
        lines = [f"{header} · {len(entries)} günde günlük, {with_metrics} günde metrik"]
        before = self._read(previous)
        for metric in metrics:
            lines.append(self._line(metric, entries, before, period, length))
        if "stress" in metrics:
            lines.append("Ölçek 1–5; stres için yüksek değer daha fazla stres demek.")
        lines.append(NO_FORECAST)
        return "\n".join(lines)

    def _read(self, days: list[date]) -> dict[date, dict]:
        """Frontmatter of each day that has a journal entry."""
        found = {}
        for day in days:
            note = self.journal.read_day(day)
            if note is not None and note[1]:
                found[day] = note[0]
        return found

    @staticmethod
    def stats(entries: dict[date, dict], metric: str) -> MetricStats:
        values = [(meta[metric], day) for day, meta in sorted(entries.items())
                  if _valid(meta.get(metric))]
        if not values:
            return MetricStats(days=0)
        low = min(values, key=lambda v: v[0])     # earliest day wins a tie
        high = max(values, key=lambda v: (v[0], -v[1].toordinal()))
        return MetricStats(len(values), sum(v for v, _ in values) / len(values), low, high)

    def _line(self, metric, entries, before, period, length) -> str:
        label = LABELS[metric]
        now = self.stats(entries, metric)
        if now.days < MIN_DAYS[period]:
            return f"{label}: yetersiz veri ({now.days} gün)"
        parts = [
            f"{label}: ort. {_num(now.average)}",
            f"en düşük {now.low[0]} ({_DAYS[now.low[1].weekday()]} {_day(now.low[1])})",
            f"en yüksek {now.high[0]} ({_DAYS[now.high[1].weekday()]} {_day(now.high[1])})",
        ]
        prev = self.stats(before, metric)
        if prev.days >= MIN_DAYS[period]:
            delta = now.average - prev.average
            if abs(delta) < STABLE_DELTA:
                change = "→ benzer"
            else:
                sign = "+" if delta > 0 else "−"
                change = f"{'↑' if delta > 0 else '↓'} {sign}{_num(abs(delta))}"
            parts.append(f"önceki {length} güne göre {change}")
        return " · ".join(parts)
