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
LifecycleHook = Callable[[], Awaitable[None]]

# Fixed text on purpose: never echo exception details (may contain secrets) to the chat.
ERROR_MESSAGE = "⚠️ Bir hata oluştu, loglara bak."

class TelegramGateway:

    def __init__(
        self,
        bot_token: str,
        allowed_chat_id: int,
        message_handler: MessageHandler,
        application: Optional[Application] = None,
        on_startup: Optional[LifecycleHook] = None,
        on_shutdown: Optional[LifecycleHook] = None,
    ) -> None:
        self.bot_token = bot_token
        self.allowed_chat_id = allowed_chat_id
        self.message_handler = message_handler
        self.on_startup = on_startup
        self.on_shutdown = on_shutdown

        if application is not None:
            self.application = application
        else:
            self.application = Application.builder().token(bot_token).build()
        # Run inside the bot's own event loop: before polling starts / after it stops.
        self.application.post_init = self._post_init
        self.application.post_shutdown = self._post_shutdown
        # Catches errors raised outside _on_update (network, polling, ...).
        self.application.add_error_handler(self._on_error)
    
    def start(self) -> None:
        """Register the message handler and start long-polling (blocks)."""
        self.application.add_handler(
            TgMessageHandler(filters.TEXT & ~filters.COMMAND, self._on_update)
        )
        self.application.run_polling()

    async def _post_init(self, application: Application) -> None:
        if self.on_startup is not None:
            await self.on_startup()

    async def _post_shutdown(self, application: Application) -> None:
        if self.on_shutdown is not None:
            await self.on_shutdown()

    async def _on_update(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Auth-check the chat id, then hand the text to the message handler."""
        chat_id = update.effective_chat.id
        text = update.effective_message.text

        if chat_id != self.allowed_chat_id:
            logger.warning("Ignored message from unauthorized chat %s", chat_id)
            return

        try:
            await self.message_handler(chat_id, text)
        except Exception:
            logger.exception("Message handler failed")
            await self._notify_error()

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log errors PTB caught outside _on_update; tell the user if it was their message."""
        logger.error("Unhandled Telegram error", exc_info=context.error)
        chat = getattr(update, "effective_chat", None)
        if chat is not None and chat.id == self.allowed_chat_id:
            await self._notify_error()

    async def _notify_error(self) -> None:
        """Send the generic error message; a failed send is logged, never raised."""
        try:
            await self.send_message(ERROR_MESSAGE)
        except Exception:
            logger.exception("Could not deliver error message to Telegram")

    async def send_message(self, text: str) -> None:
        await self.application.bot.send_message(
            chat_id=self.allowed_chat_id, text=text
        )
