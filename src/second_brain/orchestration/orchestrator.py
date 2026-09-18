"""Orchestrator — the core decision center (orchestration layer).

Chooses and invokes tools via tool-calling based on the classified intent.
"""
from __future__ import annotations


class Orchestrator:
    def __init__(self, llm, router, context, tools: list) -> None:
        self.llm = llm
        self.router = router
        self.context = context
        self.tools = {t.name: t for t in tools}

    def handle(self, message: str) -> str:
        """Classify -> build context -> call tool(s) -> compose a reply."""
        raise NotImplementedError

    def _call_tool(self, name: str, args: dict):
        return self.tools[name].run(args)
