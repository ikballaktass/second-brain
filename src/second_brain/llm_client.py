"""Thin wrapper over the LLM (Claude) and embeddings (cross-cutting)."""
from __future__ import annotations
from dataclasses import dataclass, field
import anthropic
from .config import Config
from .embeddings import Embedder, Kind


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


class LLMError(Exception):
    """Raised when the LLM call fails."""


@dataclass
class LLMResult:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient:
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=Config.secret("ANTHROPIC_API_KEY"))
        self.model = Config.get("LLM_MODEL", "claude-haiku-4-5-20251001")
        # Anthropic has no embeddings API; vectors come from a local model (lazy-loaded).
        self.embedder = Embedder.from_config()

    def complete(
        self,
        prompt: str,
        tools: list | None = None,
        system: str | None = None,
        model: str | None = None,
        max_tokens: int = 1024,
    ) -> LLMResult:
        """Call the model, optionally with tool definitions (tool-calling).

        `model` and `max_tokens` let callers (e.g. the Router) use a cheaper
        model or a tighter output budget than the default.
        """
        kwargs = {}
        if tools:
            kwargs["tools"] = tools
        if system:
            kwargs["system"] = system

        try:
            response = self.client.messages.create(
                model=model or self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                **kwargs,
            )
        except anthropic.AuthenticationError as e:
            raise LLMError("Anthropic API key is invalid.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Rate limit hit; try again in a moment.") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Anthropic API (network problem?).") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic API error ({e.status_code}): {e.message}") from e

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        tool_calls = [
            ToolCall(id=block.id, name=block.name, input=block.input)
            for block in response.content
            if block.type == "tool_use"
        ]
        return LLMResult(text=text, tool_calls=tool_calls)

    def embed(self, texts: list[str], kind: Kind = "document") -> list[list[float]]:
        """Embedding vectors for semantic recall, computed locally (see embeddings.py)."""
        return self.embedder.embed(texts, kind)
