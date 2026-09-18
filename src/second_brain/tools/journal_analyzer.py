"""Journal analyzer — end-of-day metric extraction (tools layer).

Fills a FIXED metric schema (mood/energy/productivity/stress, 1-5); it must NOT
invent new metrics. Writes results back to the note frontmatter as suggestions
the user can correct.
"""
from __future__ import annotations
from datetime import date
from ..models import DayMetrics


class JournalAnalyzer:
    def __init__(self, llm, vault, index=None) -> None:
        self.llm = llm
        self.vault = vault
        self.index = index

    def analyze(self, day: date) -> DayMetrics:
        raise NotImplementedError
