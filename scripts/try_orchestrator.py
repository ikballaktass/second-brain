"""Smoke test for Orchestrator.handle() — fake LLM, temporary vault, no API cost."""
import tempfile

from second_brain.llm_client import LLMResult, ToolCall
from second_brain.orchestration.orchestrator import Orchestrator
from second_brain.storage.vault_repository import VaultRepository
from second_brain.tools.note_writer import NoteWriter


class FakeLLM:
    """Returns a canned LLMResult instead of calling the API."""

    def __init__(self, result: LLMResult) -> None:
        self.result = result
        self.seen_tools = None
        self.seen_system = None

    def complete(self, prompt, tools=None, system=None):
        self.seen_tools = tools
        self.seen_system = system
        return self.result


with tempfile.TemporaryDirectory() as tmp:
    tools = [NoteWriter(vault=VaultRepository(tmp))]

    # 1) Plain text reply, no tool call
    llm = FakeLLM(LLMResult(text="Merhaba!"))
    orch = Orchestrator(llm=llm, router=None, context=None, tools=tools)
    print("1:", orch.handle("selam"))
    print("   tools sent:", [t["name"] for t in llm.seen_tools])
    print("   system sent:", llm.seen_system is not None)

    # 2) Model asks for a tool
    call = ToolCall(id="t1", name="note_writer", input={"title": "x", "content": "y"})
    llm = FakeLLM(LLMResult(text="", tool_calls=[call]))
    orch = Orchestrator(llm=llm, router=None, context=None, tools=tools)
    print("2:", orch.handle("bunu kaydet"))
    
    # 3) Unknown tool
    call = ToolCall(id="t2", name="fly_to_moon", input={})
    llm = FakeLLM(LLMResult(text="", tool_calls=[call]))
    orch = Orchestrator(llm=llm, router=None, context=None, tools=tools)
    print("3:", orch.handle("aya uç"))

    # 4) Tool raises (missing 'content')
    call = ToolCall(id="t3", name="note_writer", input={"title": "x"})
    llm = FakeLLM(LLMResult(text="", tool_calls=[call]))
    orch = Orchestrator(llm=llm, router=None, context=None, tools=tools)
    print("4:", orch.handle("bozuk kayıt"))
