""""Entry point — wires the layers together (Phase 0 minimal path).

message -> Orchestrator -> (tool) NoteWriter -> Vault -> reply
"""
from __future__ import annotations

import asyncio
import logging

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .config import Config, assert_outside_vault
from .interface.telegram_gateway import TelegramGateway
from .llm_client import LLMClient
from .orchestration.context_builder import ContextBuilder
from .orchestration.journal_nudger import JournalNudger
from .orchestration.orchestrator import Orchestrator
from .orchestration.recall_suggester import RecallSuggester
from .orchestration.reminder_dispatcher import ReminderDispatcher
from .orchestration.router import Router
from .proactive.policy import Policy
from .proactive.scheduler import DEFAULT_INTERVAL_S, Scheduler
from .storage.index_store import DEFAULT_INDEX_PATH, IndexStore
from .storage.state_db import StateDB
from .storage.vault_repository import VaultRepository
from .tools.calendar import CalendarTool
from .tools.journal_analyzer import JournalAnalyzer
from .tools.journal_writer import JournalWriter
from .tools.link_capturer import LinkCapturer
from .tools.note_writer import NoteWriter
from .tools.reminder_manager import ReminderManager
from .tools.retriever import Retriever
from .tools.state_manager import StateManager

logger = logging.getLogger(__name__)

FALLBACK_REPLY = "Something went wrong. Please try again."


def build() -> TelegramGateway:
    vault_path = Config.secret("VAULT_PATH")
    assert_outside_vault(vault_path)  # secrets/state must never be synced with the vault
    llm = LLMClient()
    vault = VaultRepository(vault_path)
    # Fail-closed: the index is wired only when the recall module is switched on.
    index = None
    if Config.enabled("recall"):
        index = IndexStore(embed=llm.embed, path=Config.get("INDEX_PATH", DEFAULT_INDEX_PATH))
    note_writer = NoteWriter(vault=vault, index=index)
    tools = [note_writer, LinkCapturer(llm=llm, note_writer=note_writer)]
    if index is not None:
        tools.append(Retriever(index=index))
    journal = JournalWriter(vault=vault, index=index) if Config.enabled("journal") else None
    if journal is not None:
        tools.append(journal)
    # Fail-closed: when switched on, a missing/invalid Google token stops startup.
    calendar = CalendarTool.from_config() if Config.enabled("calendar") else None
    if calendar is not None:
        tools.append(calendar)
    # Fail-closed: reminders, scheduler and send path switch on and off together.
    scheduler = None
    state = None
    if Config.enabled("proactive"):
        state = StateDB(Config.get("STATE_DB_PATH", "state.db"))
        reminders = ReminderManager(state=state)
        tools.append(reminders)
        state_manager = StateManager(note_writer=note_writer)
        tools.append(state_manager)
        policy = Policy.from_config(
            state,
            busy_until=calendar.busy_until if calendar is not None else None,
            state_flags=state_manager.current,
        )

        async def send(text: str) -> None:
            await gateway.send_message(text)

        interval = float(Config.get("SCHEDULER_INTERVAL_S", str(DEFAULT_INTERVAL_S)))
        scheduler = Scheduler(
            state=state,
            on_due=ReminderDispatcher(reminders=reminders, policy=policy, send=send),
            interval_s=interval,
        )
        if journal is not None:
            nudger = JournalNudger.from_config(journal, policy, state, send)
            scheduler.add_job("journal_check", nudger, IntervalTrigger(minutes=30))
            # Right after the 04:00 day boundary; run_now catches up days missed while down.
            analyzer = JournalAnalyzer(llm=llm, journal=journal, state=state)
            scheduler.add_job("analyze", analyzer, CronTrigger(hour=4, minute=30), run_now=True)
    context = ContextBuilder.from_config(index, vault) if index is not None else None
    # Cooldowns persist in state_db when it exists (proactive on), else in memory.
    suggester = RecallSuggester(state=state) if index is not None else None
    orch = Orchestrator(
        llm=llm, router=Router(llm), context=context, tools=tools, suggester=suggester
    )

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
