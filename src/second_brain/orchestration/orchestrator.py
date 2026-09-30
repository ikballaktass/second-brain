"""Orchestrator — the core decision center (orchestration layer).

Chooses and invokes tools via tool-calling based on the classified intent.
"""
from __future__ import annotations

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are Second Brain, a personal assistant for a single user, reached via Telegram.

- Reply in the same language the user writes in.
- Keep replies short and direct.
- When the user asks to save, note down, or remember something, call the
  note_writer tool. Give the note a short, descriptive title.
- When the message contains a URL, call the link_capturer tool with that url, the
  user's accompanying words as note, and source if they said where it came from.
- When the user wants to be reminded of something at a specific time, call the
  reminder_manager tool with action=create and a due time in ISO local format,
  computed from the current local time given below. Use action=list or action=close
  when they ask about or cancel their reminders.
- When the user asks about their schedule or events, call the calendar tool with
  action=list and the day as YYYY-MM-DD.
- When the user asks about something they saved before, first use the related notes
  given below (if any) and cite their paths. If they are missing or not enough, call
  the retriever tool to search further.
- Only use the tools you are given. If you cannot help with something, say so.
"""


def system_prompt(now: datetime) -> str:
    """SYSTEM_PROMPT plus the current local time, so the LLM can resolve "tomorrow 10am"."""
    return f"{SYSTEM_PROMPT}\nCurrent local time: {now:%Y-%m-%d %H:%M} ({now:%A}).\n"


class Orchestrator:
    def __init__(self, llm, router, context, tools: list, suggester=None) -> None:
        self.llm = llm
        self.router = router
        self.context = context
        self.tools = {t.name: t for t in tools}
        self.suggester = suggester

    def handle(self, message: str) -> str:
        """Classify -> build context -> call tool(s) -> compose a reply (+ recall hint)."""
        intent = None
        if self.router is not None:
            intent = self.router.classify(message)
            logger.info("Intent: %s", intent.value)
            # TODO(phase-1+): branch on intent (e.g. JOURNAL -> journal_writer).

        api_tools = [t.to_api() for t in self.tools.values()]

        system = system_prompt(datetime.now())
        # Built before any tool runs, so a note saved in this turn is not in it.
        context = self._build_context(message)
        related = context.render() if context is not None else ""
        if related:
            system = f"{system}\n{related}\n"

        result = self.llm.complete(message, tools=api_tools, system=system)

        if not result.tool_calls:
            reply = result.text
        else:
            replies = []
            if result.text:
                replies.append(result.text)
            for call in result.tool_calls:
                replies.append(self._call_tool(call.name, call.input))
            reply = "\n".join(replies)

        hint = self._recall_hint(context, intent, reply)
        return f"{reply}\n\n{hint}" if hint else reply

    def _build_context(self, message: str):
        """Related notes for this message, or None (no builder, or it failed)."""
        if self.context is None:
            return None
        try:
            return self.context.build(message)
        except Exception:
            logger.exception("Building context failed; answering without related notes")
            return None

    def _recall_hint(self, context, intent, reply: str) -> str | None:
        """'Reminds you of' line from the suggester; never breaks the reply."""
        if self.suggester is None:
            return None
        try:
            return self.suggester.suggest(context, intent, reply)
        except Exception:
            logger.exception("Recall suggestion failed; replying without it")
            return None

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
