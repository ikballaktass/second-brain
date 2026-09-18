"""Telegram gateway — the single bidirectional channel (interface layer).

Receives user messages AND sends proactive nudges. Only TELEGRAM_ALLOWED_CHAT_ID
is served (single-user).
"""
from __future__ import annotations


class TelegramGateway:
    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator
        # TODO: build python-telegram-bot Application with the bot token

    def start(self) -> None:
        """Start long-polling; register the message handler."""
        raise NotImplementedError

    def send(self, chat_id: str, text: str) -> None:
        """Send a message (used by both replies and proactive nudges)."""
        raise NotImplementedError

    def _on_message(self, update) -> None:
        """Auth-check the chat id, then hand off to the orchestrator."""
        raise NotImplementedError
