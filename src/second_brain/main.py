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
from .storage.vault_repository import VaultRepository
from .tools.note_writer import NoteWriter

logger = logging.getLogger(__name__)

FALLBACK_REPLY = "Something went wrong. Please try again."


def build() -> TelegramGateway:
    llm = LLMClient()
    vault = VaultRepository(Config.secret("VAULT_PATH"))
    tools = [NoteWriter(vault=vault)]
    orch = Orchestrator(llm=llm, router=None, context=None, tools=tools)

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
    )
    return gateway


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not Config.get("TELEGRAM_BOT_TOKEN"):
        logging.info("Config OK. TELEGRAM_BOT_TOKEN is not set, cannot start Telegram (Phase 0 smoke test).")
        return
    build().start()


if __name__ == "__main__":
    main()
