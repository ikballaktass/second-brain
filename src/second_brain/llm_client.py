"""Thin wrapper over the LLM (Claude) and embeddings (cross-cutting)."""
from __future__ import annotations
import anthropic
from .config import Config


class LLMClient:
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=Config.secret("ANTHROPIC_API_KEY"))
        self.model = Config.get("LLM_MODEL", "claude-haiku-4-5-20251001")

    def complete(self, prompt: str, tools: list | None = None) -> str:
        """Call the model, optionally with tool definitions (tool-calling)."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        )

    def embed(self, text: str) -> list[float]:
        """Return an embedding vector for semantic recall (Phase 4)."""
        raise NotImplementedError
