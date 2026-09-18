"""Intent router — cheap/fast classification (orchestration layer)."""
from __future__ import annotations
from ..models import Intent


class Router:
    def __init__(self, llm) -> None:
        self.llm = llm

    def classify(self, message: str) -> Intent:
        """Return the intent of an incoming message (use a cheap model)."""
        raise NotImplementedError
