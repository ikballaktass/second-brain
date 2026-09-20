from second_brain.llm_client import LLMClient, LLMError

llm = LLMClient()

# Test 1: basic call without tool
try:
    result = llm.complete("Say hello in Turkish in one short sentence.")
except LLMError as e:
    print("LLMError:", e)
else:
    print("text:", result.text)
    print("tool_calls:", result.tool_calls)

# Test 2: call with tool
tools = [
    {
        "name": "save_note",
        "description": "Save a note to the user's vault.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
]
try:
    result = llm.complete("Bunu not al: yarın market alışverişi yap.", tools=tools)
except LLMError as e:
    print("LLMError:", e)
else:
    print("text:", result.text)
    print("tool_calls:", result.tool_calls)