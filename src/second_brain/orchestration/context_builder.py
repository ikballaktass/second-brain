"""Context builder — assembles the LLM context (orchestration layer).

history + related notes (retriever) + current state; manages the token budget.
"""
from __future__ import annotations


class ContextBuilder:
    def __init__(self, state, retriever=None) -> None:
        self.state = state
        self.retriever = retriever

    def build(self, message: str) -> dict:
        """Return the context payload for the LLM."""
        raise NotImplementedError
