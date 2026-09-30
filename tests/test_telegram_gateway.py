"""Tests for TelegramGateway error reporting (issue #45)."""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

from telegram.ext import Application

from second_brain.interface.telegram_gateway import ERROR_MESSAGE, TelegramGateway

ALLOWED = 42


def _update(chat_id, text="merhaba"):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=SimpleNamespace(text=text),
    )


def _gateway(handler):
    app = Application.builder().token("123:abc").build()
    gw = TelegramGateway("123:abc", ALLOWED, message_handler=handler, application=app)
    gw.send_message = AsyncMock()
    return gw


def test_handler_error_is_reported_and_bot_keeps_running(caplog):
    received = []

    async def handler(chat_id, text):
        if text == "boom":
            raise RuntimeError("invalid api key sk-secret-123")
        received.append(text)

    gw = _gateway(handler)

    with caplog.at_level(logging.ERROR):
        asyncio.run(gw._on_update(_update(ALLOWED, "boom"), None))

    gw.send_message.assert_awaited_once_with(ERROR_MESSAGE)
    assert "sk-secret" not in ERROR_MESSAGE
    record = next(r for r in caplog.records if r.message == "Message handler failed")
    assert record.exc_info is not None  # full traceback is logged

    # next message is still handled normally
    asyncio.run(gw._on_update(_update(ALLOWED, "sonraki"), None))
    assert received == ["sonraki"]
    assert gw.send_message.await_count == 1


def test_failed_error_delivery_does_not_raise(caplog):
    async def handler(chat_id, text):
        raise RuntimeError("llm timeout")

    gw = _gateway(handler)
    gw.send_message.side_effect = ConnectionError("network down")

    with caplog.at_level(logging.ERROR):
        asyncio.run(gw._on_update(_update(ALLOWED), None))

    assert any(r.message == "Could not deliver error message to Telegram" for r in caplog.records)


def test_unauthorized_chat_is_ignored_without_error_message():
    handler = AsyncMock(side_effect=RuntimeError("should not run"))
    gw = _gateway(handler)

    asyncio.run(gw._on_update(_update(999), None))

    handler.assert_not_awaited()
    gw.send_message.assert_not_awaited()


def test_error_handler_is_registered():
    gw = _gateway(AsyncMock())
    registered = gw.application.error_handlers
    assert gw._on_error in registered


def test_error_without_update_is_only_logged(caplog):
    gw = _gateway(AsyncMock())
    context = SimpleNamespace(error=ConnectionError("network down"))

    with caplog.at_level(logging.ERROR):
        asyncio.run(gw._on_error(None, context))

    gw.send_message.assert_not_awaited()
    record = next(r for r in caplog.records if r.message == "Unhandled Telegram error")
    assert record.exc_info[1] is context.error


def test_error_for_allowed_chat_notifies_user():
    gw = _gateway(AsyncMock())
    context = SimpleNamespace(error=RuntimeError("x"))

    asyncio.run(gw._on_error(_update(ALLOWED), context))

    gw.send_message.assert_awaited_once_with(ERROR_MESSAGE)


def test_error_for_foreign_chat_does_not_notify():
    gw = _gateway(AsyncMock())
    context = SimpleNamespace(error=RuntimeError("x"))

    asyncio.run(gw._on_error(_update(999), context))

    gw.send_message.assert_not_awaited()
