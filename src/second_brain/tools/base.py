"""Tool interface — every capability implements this (tools layer)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]

    @abstractmethod
    def run(self, args: dict):
        """Execute the capability and return a result."""

    def to_api(self) -> dict[str, Any]:
        """Return this tool's definition in Antrophic's 'tools' format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
