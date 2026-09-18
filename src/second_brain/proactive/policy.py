"""Policy — the 'whether/when to nudge' engine (proactive layer).

Rule-based to start: quiet hours, calendar busyness, state flags, rate limiting.
Deliberately conservative so the assistant never becomes annoying.
"""
from __future__ import annotations


class Policy:
    def should_notify(self, ctx: dict) -> bool:
        """Return True only if it is a good moment to message the user."""
        raise NotImplementedError

    def next_window(self):
        """Return the next suitable time to try again."""
        raise NotImplementedError
