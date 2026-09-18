"""Tool interface — every capability implements this (tools layer)."""
from __future__ import annotations
from abc import ABC, abstractmethod


class Tool(ABC):
    name: str

    @abstractmethod
    def run(self, args: dict):
        """Execute the capability and return a result."""
        raise NotImplementedError
