"""Semantic search over the vault (Phase 4). Thin tool over IndexStore.search."""
from __future__ import annotations

from ..storage.index_store import IndexStore
from .base import Tool

DEFAULT_K = 5
MAX_K = 10


class Retriever(Tool):
    name = "retriever"
    description = (
        "Search the user's saved notes and bookmarks by meaning. Use when the user asks "
        "about something they saved before or wants to find/recall a note. Returns the "
        "closest notes with their vault path, similarity score (0-1) and a short excerpt."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to look for, in natural language"},
            "k": {"type": "integer", "description": f"How many notes (1-{MAX_K}, default 5)"},
        },
        "required": ["query"],
    }

    def __init__(self, index: IndexStore) -> None:
        self.index = index

    def run(self, args: dict) -> str:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("'query' is required")
        k = args.get("k", DEFAULT_K)
        if not isinstance(k, int) or isinstance(k, bool):
            raise ValueError("'k' must be an integer")
        hits = self.index.search(query.strip(), max(1, min(k, MAX_K)))
        if not hits:
            return "No matching notes."
        return "\n".join(f"{h.path} · {h.score:.2f} · {h.snippet}" for h in hits)
