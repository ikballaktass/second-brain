"""Manual check for VaultRepository (Issue 3)."""
import tempfile

from second_brain.storage.vault_repository import VaultRepository


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo = VaultRepository(tmp)
        note = repo.root / "notes/deneme.md"

        # 1. write
        repo.write("notes/deneme.md", "Merhaba dünya", {"type": "note", "tags": ["a"]})
        text = note.read_text(encoding="utf-8")
        assert "type: note" in text
        assert "Merhaba dünya" in text
        print("1. write: OK")

        # 2. upsert
        repo.upsert_frontmatter("notes/deneme.md", {"tags": ["a", "b"], "status": "done"})
        text = note.read_text(encoding="utf-8")
        assert "- b" in text
        assert "status: done" in text
        assert "type: note" in text
        assert "Merhaba dünya" in text
        print("2. upsert: OK")

        # 3. path guard
        try:
            repo.write("../kacis.md", "x")
        except ValueError:
            print("3. path guard: OK")
        else:
            raise AssertionError("path escape was not blocked")

        # 4. missing file
        try:
            repo.upsert_frontmatter("yok.md", {"a": 1})
        except FileNotFoundError:
            print("4. missing file: OK")
        else:
            raise AssertionError("missing file was not detected")

    print("All checks passed")


if __name__ == "__main__":
    main()