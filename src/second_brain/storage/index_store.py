"""Index store — embedding index, DERIVED from the vault (storage layer).

Can be deleted and rebuilt from the vault at any time.
"""
from __future__ import annotations


class IndexStore:
    def __init__(self, llm=None) -> None:
        self.llm = llm

    def upsert(self, note) -> None:
        raise NotImplementedError

    def search(self, query: str, k: int = 5) -> list:
        raise NotImplementedError
