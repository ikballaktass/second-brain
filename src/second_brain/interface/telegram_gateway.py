"""Telegram gateway — the single bidirectional channel (interface layer).

Receives user messages AND sends proactive nudges. Only TELEGRAM_ALLOWED_CHAT_ID
is served (single-user).
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler as TgMessageHandler, filters

import logging

logger = logging.getLogger(__name__)

MessageHandler = Callable[[int, str], Awaitable[None]]

class TelegramGateway:

    def __init__(
        self,
        bot_token: str,
        allowed_chat_id: int,
        message_handler: MessageHandler,
        application: Optional[Application] = None,
    ) -> None:
        self.bot_token = bot_token
        self.allowed_chat_id = allowed_chat_id
        self.message_handler = message_handler

        if application is not None:
            self.application = application
        else:
            self.application = Application.builder().token(bot_token).build()
    
    def start(self) -> None:
        """Register the message handler and start long-polling (blocks)."""
        self.application.add_handler(
            TgMessageHandler(filters.TEXT & ~filters.COMMAND, self._on_update)
        )
        self.application.run_polling()

    async def _on_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Auth-check the chat id, then hand the text to the message handler."""
        chat_id = update.effective_chat.id
        text = update.effective_message.text

        if chat_id != self.allowed_chat_id:
            logger.warning("Ignored message from unauthorized chat %s", chat_id)
            return

        await self.message_handler(chat_id, text)

    async def send_message(self, text: str) -> None:
        await self.application.bot.send_message(
            chat_id=self.allowed_chat_id, text=text
        )
