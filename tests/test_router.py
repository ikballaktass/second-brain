"""Router tests — no network: a fake LLM stands in for Claude."""
import pytest

from second_brain.llm_client import LLMError, LLMResult
from second_brain.models import Intent
from second_brain.orchestration.router import Router


class FakeLLM:
    """Returns a canned answer (or raises) and records how it was called."""

    def __init__(self, reply: str = "", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    def complete(self, prompt, tools=None, system=None, model=None, max_tokens=1024):
        self.calls.append(
            {"prompt": prompt, "system": system, "model": model, "max_tokens": max_tokens}
        )
        if self.error:
            raise self.error
        return LLMResult(text=self.reply)


# --- the happy path: every label the model can return maps to its Intent ---

@pytest.mark.parametrize("intent", list(Intent))
def test_each_label_maps_to_intent(intent):
    router = Router(FakeLLM(reply=intent.value))
    assert router.classify("herhangi bir mesaj") is intent


# --- tolerant parsing: noisy model output still yields the right label ---

@pytest.mark.parametrize("raw, expected", [
    ("Capture.", Intent.CAPTURE),
    ("  reminder\n", Intent.REMINDER),
    ("label: task", Intent.TASK),
    ("JOURNAL", Intent.JOURNAL),
])
def test_parse_tolerates_noise(raw, expected):
    assert Router._parse(raw) is expected


# --- fallbacks: never crash, fall back to CAPTURE ---

@pytest.mark.parametrize("raw", ["", "banana", "I am not sure"])
def test_unknown_label_falls_back_to_capture(raw):
    assert Router._parse(raw) is Intent.CAPTURE


def test_llm_error_falls_back_to_capture():
    router = Router(FakeLLM(error=LLMError("network down")))
    assert router.classify("Faturayı öde") is Intent.CAPTURE


def test_empty_message_skips_llm():
    llm = FakeLLM(reply="query")
    assert Router(llm).classify("   ") is Intent.CAPTURE
    assert llm.calls == []


# --- shortcuts and call shape ---

def test_slash_command_skips_llm():
    llm = FakeLLM(reply="capture")
    assert Router(llm).classify("/quiet") is Intent.COMMAND
    assert llm.calls == []


def test_uses_cheap_call_settings(monkeypatch):
    monkeypatch.setenv("ROUTER_MODEL", "cheap-model")
    llm = FakeLLM(reply="query")
    Router(llm).classify("Geçen hafta ne kaydettim?")

    call = llm.calls[0]
    assert call["model"] == "cheap-model"
    assert call["max_tokens"] == 10
    assert call["system"]  # router prompt was sent
    assert call["prompt"] == "Geçen hafta ne kaydettim?"
    