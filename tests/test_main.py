"""main.py imports cleanly and build() wires the Router into the Orchestrator."""
import importlib

import pytest

from second_brain import main as main_module
from second_brain.orchestration.router import Router


@pytest.fixture(autouse=True)
def temp_state_db(monkeypatch, tmp_path):
    """proactive is on by default: keep build() from creating ./state.db in the repo."""
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))


class FakeEmbeddingLLM:
    def embed(self, texts, kind="document"):
        return [[1.0, 0.0] for _ in texts]


class FakeGateway:
    def __init__(self, bot_token, allowed_chat_id, message_handler, **hooks) -> None:
        self.message_handler = message_handler


def test_module_imports_without_error():
    importlib.reload(main_module)


def test_build_wires_router(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123")
    monkeypatch.setattr(main_module, "LLMClient", lambda: object())
    monkeypatch.setattr(main_module, "TelegramGateway", FakeGateway)

    captured = {}
    real_orchestrator = main_module.Orchestrator

    def spy(**kwargs):
        captured.update(kwargs)
        return real_orchestrator(**kwargs)

    monkeypatch.setattr(main_module, "Orchestrator", spy)

    gateway = main_module.build()

    assert isinstance(gateway, FakeGateway)
    assert isinstance(captured["router"], Router)
    assert captured["router"].llm is captured["llm"]


def test_build_registers_link_capturer_sharing_note_writer(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123")
    monkeypatch.setattr(main_module, "LLMClient", lambda: object())
    monkeypatch.setattr(main_module, "TelegramGateway", FakeGateway)
    captured = {}
    real_orchestrator = main_module.Orchestrator
    monkeypatch.setattr(
        main_module, "Orchestrator",
        lambda **kw: captured.update(kw) or real_orchestrator(**kw),
    )

    main_module.build()

    tools = {t.name: t for t in captured["tools"]}
    assert set(tools) == {"note_writer", "link_capturer", "reminder_manager", "journal_writer", "state_manager",
                          "trend_report"}
    assert tools["link_capturer"].note_writer is tools["note_writer"]
    assert tools["note_writer"].index is None  # recall is off -> fail-closed


def test_build_wires_index_when_recall_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "123")
    monkeypatch.setenv("INDEX_PATH", str(tmp_path / "index"))
    monkeypatch.setattr(main_module, "LLMClient", FakeEmbeddingLLM)
    monkeypatch.setattr(main_module, "TelegramGateway", FakeGateway)
    monkeypatch.setitem(main_module.Config.manifest, "recall", True)
    captured = {}
    real_orchestrator = main_module.Orchestrator
    monkeypatch.setattr(
        main_module, "Orchestrator",
        lambda **kw: captured.update(kw) or real_orchestrator(**kw),
    )

    main_module.build()

    writer = next(t for t in captured["tools"] if t.name == "note_writer")
    assert isinstance(writer.index, main_module.IndexStore)
