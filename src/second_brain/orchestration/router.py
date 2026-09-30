"""Intent router — cheap/fast classification (orchestration layer)."""
from __future__ import annotations

import logging
import re

from ..config import Config
from ..llm_client import LLMError
from ..models import Intent
from ..prompts import ROUTER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_ROUTER_MODEL = "claude-haiku-4-5-20251001"
FALLBACK_INTENT = Intent.CAPTURE
_VALID_LABELS = {i.value for i in Intent}


class Router:
    def __init__(self, llm) -> None:
        self.llm = llm
        self.model = Config.get("ROUTER_MODEL", DEFAULT_ROUTER_MODEL)

    def classify(self, message: str) -> Intent:
        """Return the intent of an incoming message (use a cheap model)."""
        text = message.strip()
        if not text:
            return FALLBACK_INTENT

        # Telegram slash commands (/start, /quiet ...) need no LLM call.
        if text.startswith("/"):
            return Intent.COMMAND

        try:
            result = self.llm.complete(
                text,
                system=ROUTER_SYSTEM_PROMPT,
                model=self.model,
                max_tokens=10,
            )
        except LLMError:
            logger.warning("Router LLM call failed; falling back to %s", FALLBACK_INTENT.value)
            return FALLBACK_INTENT

        return self._parse(result.text)

    @staticmethod
    def _parse(raw: str) -> Intent:
        """Turn the model's raw answer into an Intent, tolerating noise like 'Capture.'"""
        for word in re.findall(r"[a-z]+", raw.lower()):
            if word in _VALID_LABELS:
                return Intent(word)

        logger.warning("Router got unparseable label %r; falling back to %s",
                       raw, FALLBACK_INTENT.value)
        return FALLBACK_INTENT