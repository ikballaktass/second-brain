"""Orchestrator calls the Router before the LLM, and still works without one."""
from second_brain.llm_client import LLMResult
from second_brain.models import Intent
from second_brain.orchestration.orchestrator import Orchestrator


class FakeLLM:
    def complete(self, prompt, tools=None, system=None, model=None, max_tokens=1024):
        return LLMResult(text="ok")


class FakeRouter:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def classify(self, message: str) -> Intent:
        self.seen.append(message)
        return Intent.JOURNAL


def test_handle_classifies_and_logs_intent(caplog):
    router = FakeRouter()
    orch = Orchestrator(llm=FakeLLM(), router=router, context=None, tools=[])

    with caplog.at_level("INFO"):
        reply = orch.handle("Bugün güzel bir gündü")

    assert router.seen == ["Bugün güzel bir gündü"]
    assert "Intent: journal" in caplog.text
    assert reply == "ok"


def test_handle_works_without_router():
    orch = Orchestrator(llm=FakeLLM(), router=None, context=None, tools=[])
    assert orch.handle("merhaba") == "ok"
    