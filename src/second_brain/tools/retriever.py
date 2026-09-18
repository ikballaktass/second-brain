"""Semantic search over the vault (Phase 4)."""
from __future__ import annotations
from .base import Tool


class Retriever(Tool):
    name = "retriever"

    def __init__(self, index=None) -> None:
        self.index = index

    def run(self, args: dict):
        raise NotImplementedError
