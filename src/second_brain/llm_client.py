"""Thin wrapper over the LLM (Claude) and embeddings (cross-cutting)."""
from __future__ import annotations
from dataclasses import dataclass, field
import anthropic
from .config import Config


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


class LLMError(Exception):
    """Raised when the LLM call fails."""


@dataclass
class LLMResult:
    test: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient:
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=Config.secret("ANTHROPIC_API_KEY"))
        self.model = Config.get("LLM_MODEL", "claude-haiku-4-5-20251001")

    def complete(self, prompt: str, tools: list | None = None) -> str:
        """Call the model, optionally with tool definitions (tool-calling)."""
        kwargs = {}
        if tools:
            kwargs["tools"] = tools

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
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

    def embed(self, text: str) -> list[float]:
        """Return an embedding vector for semantic recall (Phase 4)."""
        raise NotImplementedError
