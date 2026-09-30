""""Entry point — wires the layers together (Phase 0 minimal path).

message -> Orchestrator -> (tool) NoteWriter -> Vault -> reply
"""
from __future__ import annotations

import asyncio
import logging

from .config import Config
from .interface.telegram_gateway import TelegramGateway
from .llm_client import LLMClient
from .orchestration.orchestrator import Orchestrator
from .orchestration.router import Router
from .proactive.scheduler import DEFAULT_INTERVAL_S, Scheduler
from .storage.index_store import IndexStore
from .storage.state_db import StateDB
from .storage.vault_repository import VaultRepository
from .tools.link_capturer import LinkCapturer
from .tools.note_writer import NoteWriter
from .tools.reminder_manager import ReminderManager

logger = logging.getLogger(__name__)

FALLBACK_REPLY = "Something went wrong. Please try again."


def build() -> TelegramGateway:
    llm = LLMClient()
    vault = VaultRepository(Config.secret("VAULT_PATH"))
    # Fail-closed: the index is wired only when the recall module is switched on.
    index = IndexStore(llm) if Config.enabled("recall") else None
    note_writer = NoteWriter(vault=vault, index=index)
    tools = [note_writer, LinkCapturer(llm=llm, note_writer=note_writer)]
    # Fail-closed: reminders are only offered once something can actually send them.
    scheduler = None
    if Config.enabled("proactive"):
        state = StateDB(Config.get("STATE_DB_PATH", "state.db"))
        tools.append(ReminderManager(state=state))
        interval = float(Config.get("SCHEDULER_INTERVAL_S", str(DEFAULT_INTERVAL_S)))
        # on_due (policy check + send) is wired in #16.
        scheduler = Scheduler(state=state, interval_s=interval)
    orch = Orchestrator(llm=llm, router=Router(llm), context=None, tools=tools)

    async def on_message(chat_id: int, text: str) -> None:
        try:
            reply = await asyncio.to_thread(orch.handle, text)
        except Exception:
            logger.exception("Failed to handle message")
            reply = FALLBACK_REPLY
        await gateway.send_message(reply or FALLBACK_REPLY)

    gateway = TelegramGateway(
        bot_token=Config.secret("TELEGRAM_BOT_TOKEN"),
        allowed_chat_id=int(Config.secret("TELEGRAM_ALLOWED_CHAT_ID")),
        message_handler=on_message,
        on_startup=_async(scheduler.start) if scheduler else None,
        on_shutdown=_async(scheduler.shutdown) if scheduler else None,
    )
    return gateway


def _async(func):
    """Wrap a sync callable as a no-arg coroutine function for gateway hooks."""
    async def hook() -> None:
        func()

    return hook


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not Config.get("TELEGRAM_BOT_TOKEN"):
        logging.info("Config OK. TELEGRAM_BOT_TOKEN is not set, cannot start Telegram (Phase 0 smoke test).")
        return
    build().start()


if __name__ == "__main__":
    main()
