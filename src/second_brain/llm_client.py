"""Thin wrapper over the LLM (Claude) and embeddings (cross-cutting)."""
from __future__ import annotations
from .config import Config


class LLMClient:
    def __init__(self) -> None:
        # TODO: init anthropic client with Config.secret("ANTHROPIC_API_KEY")
        ...

    def complete(self, prompt: str, tools: list | None = None) -> str:
        """Call the model, optionally with tool definitions (tool-calling)."""
        raise NotImplementedError

    def embed(self, text: str) -> list[float]:
        """Return an embedding vector for semantic recall (Phase 4)."""
        raise NotImplementedError
