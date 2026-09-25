"""Orchestrator — the core decision center (orchestration layer).

Chooses and invokes tools via tool-calling based on the classified intent.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Second Brain, a personal assistant for a single user, reached via Telegram.

- Reply in the same language the user writes in.
- Keep replies short and direct.
- When the user asks to save, note down, or remember something, call the
  note_writer tool. Give the note a short, descriptive title.
- Only use the tools you are given. If you cannot help with something, say so.
"""


class Orchestrator:
    def __init__(self, llm, router, context, tools: list) -> None:
        self.llm = llm
        self.router = router
        self.context = context
        self.tools = {t.name: t for t in tools}

    def handle(self, message: str) -> str:
        """Classify -> build context -> call tool(s) -> compose a reply."""
        api_tools = [t.to_api() for t in self.tools.values()]

        result = self.llm.complete(message, tools=api_tools, system=SYSTEM_PROMPT)

        if not result.tool_calls:
            return result.text

        replies = []
        if result.text:
            replies.append(result.text)
        
        for call in result.tool_calls:
            replies.append(self._call_tool(call.name, call.input))        
        
        return "\n".join(replies)

    def _call_tool(self, name: str, args: dict):
        if name not in self.tools:
            logger.warning("Unknown tool requested: %s", name)
            return f"Unknown tool: {name}"
        
        try:
            return self.tools[name].run(args)

        except ValueError as err:
            return f"Could not run {name}: {err}"
            
        except Exception:
            logger.exception("Tool %s raised an exception", name)
            return f"Tool {name} failed to run."
