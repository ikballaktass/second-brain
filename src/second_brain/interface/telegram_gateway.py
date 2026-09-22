"""Telegram gateway — the single bidirectional channel (interface layer).

Receives user messages AND sends proactive nudges. Only TELEGRAM_ALLOWED_CHAT_ID
is served (single-user).
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler as TgMessageHandler, filters

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
    
    async def start(self) -> None:
        """Start long-polling; register the message handler."""
        self.application.add_handler(
            TgMessageHandler(filters.ALL, self._on_update)
        )
        await self.application.run_polling()

    async def _on_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        
        chat_id = update.effective_chat.id
        text = update.effective_message.text

        if chat_id != self.allowed_chat_id:
            return

        await self.message_handler(chat_id, text)

    async def send_message(self, text: str) -> None:
        await self.application.bot.send_message(
            chat_id=self.allowed_chat_id, text=text
        )
