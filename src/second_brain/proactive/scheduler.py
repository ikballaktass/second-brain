"""Scheduler — background loop that drives proactive behavior (proactive layer).

Every N seconds: due reminders? journal empty? morning brief? If a condition
fires, it passes through Policy before anything is sent.
"""
from __future__ import annotations


class Scheduler:
    def __init__(self, policy, orchestrator, state, analyzer=None) -> None:
        self.policy = policy
        self.orchestrator = orchestrator
        self.state = state
        self.analyzer = analyzer

    def tick(self) -> None:
        """One pass of the loop (called by APScheduler)."""
        raise NotImplementedError

    def add_job(self, job) -> None:
        raise NotImplementedError
