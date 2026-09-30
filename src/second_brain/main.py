""""Entry point — wires the layers together (Phase 0 minimal path).

message -> Orchestrator -> (tool) NoteWriter -> Vault -> reply
"""
from __future__ import annotations

import asyncio
import logging

from .config import Config, assert_outside_vault
from .interface.telegram_gateway import TelegramGateway
from .llm_client import LLMClient
from .orchestration.orchestrator import Orchestrator
from .orchestration.reminder_dispatcher import ReminderDispatcher
from .orchestration.router import Router
from .proactive.policy import Policy
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
    vault_path = Config.secret("VAULT_PATH")
    assert_outside_vault(vault_path)  # secrets/state must never be synced with the vault
    llm = LLMClient()
    vault = VaultRepository(vault_path)
    # Fail-closed: the index is wired only when the recall module is switched on.
    index = IndexStore(llm) if Config.enabled("recall") else None
    note_writer = NoteWriter(vault=vault, index=index)
    tools = [note_writer, LinkCapturer(llm=llm, note_writer=note_writer)]
    # Fail-closed: reminders, scheduler and send path switch on and off together.
    scheduler = None
    if Config.enabled("proactive"):
        state = StateDB(Config.get("STATE_DB_PATH", "state.db"))
        reminders = ReminderManager(state=state)
        tools.append(reminders)
        # busy_until (calendar) is added once CalendarTool exists (#11).
        policy = Policy.from_config(state)

        async def send(text: str) -> None:
            await gateway.send_message(text)

        interval = float(Config.get("SCHEDULER_INTERVAL_S", str(DEFAULT_INTERVAL_S)))
        scheduler = Scheduler(
            state=state,
            on_due=ReminderDispatcher(reminders=reminders, policy=policy, send=send),
            interval_s=interval,
        )
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
