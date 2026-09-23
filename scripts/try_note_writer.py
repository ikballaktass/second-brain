"""Smoke test for NoteWriter — uses a temporary vault, no LLM."""
import tempfile
from pathlib import Path

from second_brain.storage.vault_repository import VaultRepository
from second_brain.tools.note_writer import NoteWriter

with tempfile.TemporaryDirectory() as tmp:
    tool = NoteWriter(vault=VaultRepository(tmp))

    print(tool.to_api()["name"])
    result = tool.run({"title": "Ekim/Kasım planı", "content": "İlk not **denemesi**."})
    print(result)

    for f in Path(tmp).rglob("*.md"):
        print("---", f.relative_to(tmp))
        print(f.read_text(encoding="utf-8"))
