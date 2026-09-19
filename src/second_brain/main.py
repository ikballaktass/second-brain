"""Entry point — wires the layers together (Phase 0 minimal path).

message -> Router -> Orchestrator -> (tool) NoteWriter -> Vault -> reply
"""
from __future__ import annotations
from .config import Config
from .llm_client import LLMClient
from .storage.vault_repository import VaultRepository
from .storage.state_db import StateDB
from .orchestration.router import Router
from .orchestration.orchestrator import Orchestrator
from .orchestration.context_builder import ContextBuilder
from .tools.note_writer import NoteWriter
from .interface.telegram_gateway import TelegramGateway

import logging


def build() -> TelegramGateway:
    llm = LLMClient()
    vault = VaultRepository(Config.secret("VAULT_PATH"))
    state = StateDB()
    ctx = ContextBuilder(state=state)
    tools = [NoteWriter(vault=vault)]  # add more tools per phase
    orch = Orchestrator(llm=llm, router=Router(llm), context=ctx, tools=tools)
    return TelegramGateway(orchestrator=orch)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if not Config.get("TELEGRAM_BOT_TOKEN"):
        logging.info("Config OK. TELEGRAM_BOT_TOKEN does not exist, cannot initialize Telegram (Phase 0 smoke test).")
        return
    build().start()


if __name__ == "__main__":
    main()
