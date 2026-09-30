"""Rebuild the semantic index from the vault (the index is derived; this is the proof).

    PYTHONPATH=src python scripts/rebuild_index.py
    PYTHONPATH=src python scripts/rebuild_index.py --query "uzay asansörü"

Needs VAULT_PATH (and optionally INDEX_PATH, EMBEDDING_MODEL). No API key: embeddings
are computed locally. The first run downloads the embedding model (~470 MB).
"""
from __future__ import annotations

import argparse
import sys
import time

from second_brain.config import Config, assert_outside_vault
from second_brain.embeddings import Embedder
from second_brain.storage.index_store import DEFAULT_INDEX_PATH, IndexStore
from second_brain.storage.vault_repository import VaultRepository


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--query", help="run a test search after rebuilding")
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()

    vault_path = Config.secret("VAULT_PATH")
    assert_outside_vault(vault_path)
    embedder = Embedder.from_config()
    index = IndexStore(embed=embedder.embed, path=Config.get("INDEX_PATH", DEFAULT_INDEX_PATH))

    started = time.monotonic()
    notes = index.rebuild(VaultRepository(vault_path))
    print(f"Indexed {notes} notes ({index.count()} chunks) in {time.monotonic() - started:.1f}s "
          f"with {embedder.model_name}")

    if args.query:
        for hit in index.search(args.query, args.k):
            print(f"{hit.score:.2f}  {hit.path}\n      {hit.snippet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
