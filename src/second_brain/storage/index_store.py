"""Index store — embedding index, DERIVED from the vault (storage layer).

Can be deleted and rebuilt from the vault at any time (`rebuild`). Notes are split
into paragraph chunks; each chunk is one vector in a local chromadb collection with
metadata {path, type, title, tags, created}. `search` returns the best chunk per note.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable

import frontmatter

from ..models import Bookmark, JournalEntry
from .vault_repository import VaultRepository

logger = logging.getLogger(__name__)

COLLECTION = "notes"
DEFAULT_INDEX_PATH = ".index"
# The default model reads at most 128 word pieces; Turkish words split into several,
# so chunks stay short enough that little is cut off.
CHUNK_CHARS = 500
SNIPPET_CHARS = 160
CANDIDATES_PER_HIT = 4
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6} ")

EmbedFn = Callable[[list[str], str], list[list[float]]]


@dataclass(frozen=True)
class SearchHit:
    path: str
    score: float      # cosine similarity, higher is closer (max 1.0)
    snippet: str
    type: str
    title: str


def _chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split on blank lines, pack paragraphs up to `limit` chars, hard-split longer ones."""
    pieces: list[str] = []
    for para in (p.strip() for p in re.split(r"\n\s*\n", text)):
        if not para:
            continue
        while len(para) > limit:
            cut = para.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            pieces.append(para[:cut].strip())
            para = para[cut:].strip()
        if para:
            pieces.append(para)

    chunks: list[str] = []
    for piece in pieces:
        if chunks and len(chunks[-1]) + 2 + len(piece) <= limit:
            chunks[-1] = f"{chunks[-1]}\n\n{piece}"
        else:
            chunks.append(piece)
    return chunks


def _note_type(note) -> str:
    if isinstance(note, Bookmark):
        return "bookmark"
    if isinstance(note, JournalEntry):
        return "journal"
    return "note"


def _title_from_path(path: str) -> str:
    return _STAMP.sub("", PurePosixPath(path).stem)


def _snippet(chunk: str) -> str:
    flat = re.sub(r"\s+", " ", chunk).strip()
    return flat if len(flat) <= SNIPPET_CHARS else flat[: SNIPPET_CHARS - 1].rstrip() + "…"


class IndexStore:
    def __init__(self, embed: EmbedFn, path: str = DEFAULT_INDEX_PATH, client=None) -> None:
        import chromadb
        from chromadb.config import Settings

        self.embed = embed
        self.path = path
        self._client = client or chromadb.PersistentClient(
            path=path, settings=Settings(anonymized_telemetry=False)
        )
        self._collection = self._open()

    def _open(self):
        # We always pass vectors ourselves, so chroma's own embedding function stays off.
        return self._client.get_or_create_collection(
            COLLECTION, metadata={"hnsw:space": "cosine"}, embedding_function=None
        )

    def count(self) -> int:
        """Number of chunks (not notes) in the index."""
        return self._collection.count()

    # --- writing -------------------------------------------------------------------------

    def upsert(self, note) -> None:
        """(Re)index a Note/Bookmark that has a vault `path`; old chunks are replaced."""
        if not note.path:
            raise ValueError("note has no vault path; save it before indexing")
        self._index(
            path=note.path,
            title=note.title or _title_from_path(note.path),
            content=note.content,
            tags=list(note.tags),
            summary=getattr(note, "summary", ""),
            note_type=_note_type(note),
            created=note.created.isoformat(timespec="seconds"),
        )

    def remove(self, path: str) -> None:
        self._collection.delete(where={"path": path})

    def rebuild(self, vault: VaultRepository) -> int:
        """Drop the index and re-embed every note in the vault; return notes indexed."""
        self._client.delete_collection(COLLECTION)
        self._collection = self._open()
        indexed = 0
        for path in vault.list():
            try:
                post = frontmatter.loads(vault.read(path))
            except Exception:
                logger.warning("Skipping unreadable note %s", path, exc_info=True)
                continue
            meta = post.metadata
            tags = meta.get("tags") or []
            self._index(
                path=path,
                title=_title_from_path(path),
                content=post.content,
                tags=[str(t) for t in tags] if isinstance(tags, list) else [str(tags)],
                summary=str(meta.get("summary") or ""),
                note_type=str(meta.get("type") or "note"),
                created=str(meta.get("created") or meta.get("date") or ""),
            )
            indexed += 1
        return indexed

    def _index(self, *, path, title, content, tags, summary, note_type, created) -> None:
        header = [title]
        if summary:
            header.append(summary)
        if tags:
            header.append("Tags: " + ", ".join(tags))
        chunks = _chunks("\n\n".join([*header, content]))

        self.remove(path)
        if not chunks:
            return
        vectors = self.embed(chunks, "document")
        meta = {"path": path, "type": note_type, "title": title, "tags": ", ".join(tags),
                "created": created}
        self._collection.add(
            ids=[f"{path}#{i}" for i in range(len(chunks))],
            embeddings=vectors,
            documents=chunks,
            metadatas=[dict(meta, chunk=i) for i in range(len(chunks))],
        )

    # --- reading -------------------------------------------------------------------------

    def search(self, query: str, k: int = 5) -> list[SearchHit]:
        """Top-k notes for a natural-language query, best first, one hit per note."""
        if not query.strip() or k < 1:
            return []
        total = self.count()
        if total == 0:
            return []
        result = self._collection.query(
            query_embeddings=self.embed([query], "query"),
            n_results=min(total, k * CANDIDATES_PER_HIT),
            include=["documents", "metadatas", "distances"],
        )
        hits: dict[str, SearchHit] = {}
        for doc, meta, distance in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0]
        ):
            if meta["path"] in hits:
                continue  # results come best-first; keep each note's best chunk
            hits[meta["path"]] = SearchHit(
                path=meta["path"], score=round(1.0 - distance, 4), snippet=_snippet(doc),
                type=meta.get("type", "note"), title=meta.get("title", ""),
            )
            if len(hits) == k:
                break
        return list(hits.values())
