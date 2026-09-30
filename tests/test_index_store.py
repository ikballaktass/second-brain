"""IndexStore + Retriever + vault read/list. A deterministic bag-of-words embedder stands in
for the real model, so tests run offline; one opt-in test uses the real model."""
import hashlib
import math
import os
import re

import pytest

from second_brain import config as config_module
from second_brain import main as main_module
from second_brain.embeddings import Embedder
from second_brain.models import Bookmark, Note
from second_brain.storage.index_store import CHUNK_CHARS, IndexStore, _chunks
from second_brain.storage.vault_repository import VaultRepository
from second_brain.tools.note_writer import NoteWriter
from second_brain.tools.retriever import Retriever

DIM = 256


def fake_embed(texts, kind="document"):
    """Hash each word into a bucket; cosine then tracks word overlap."""
    vectors = []
    for text in texts:
        v = [0.0] * DIM
        for word in re.findall(r"\w+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        vectors.append([x / norm for x in v])
    return vectors


@pytest.fixture
def vault(tmp_path):
    return VaultRepository(str(tmp_path / "vault"))


@pytest.fixture
def index(tmp_path):
    return IndexStore(embed=fake_embed, path=str(tmp_path / "index"))


def note(path, content, title="t", tags=(), **kw):
    return Note(title=title, content=content, tags=list(tags), path=path, **kw)


# --- vault read/list ----------------------------------------------------------------------

def test_vault_read_and_list(vault):
    vault.write("notes/b.md", "B", {"type": "note"})
    vault.write("00-Gelen/a.md", "A")
    vault.write(".obsidian/plugins.md", "hidden")
    vault.write(".trash/old.md", "trashed")
    vault.write("notes/.draft.md", "hidden file")
    (vault.root / "notes" / "image.png").write_bytes(b"x")

    assert vault.list() == ["00-Gelen/a.md", "notes/b.md"]
    assert vault.list("notes") == ["notes/b.md"]
    assert vault.list("missing") == []
    assert "type: note" in vault.read("notes/b.md")
    with pytest.raises(FileNotFoundError):
        vault.read("notes/none.md")
    with pytest.raises(ValueError):
        vault.read("../outside.md")


# --- chunking -----------------------------------------------------------------------------

def test_chunks_pack_paragraphs_and_split_long_ones():
    assert _chunks("") == []
    assert _chunks("tek paragraf") == ["tek paragraf"]
    assert _chunks("a\n\nb\n\n\n c ") == ["a\n\nb\n\nc"]

    long_para = " ".join(["kelime"] * 300)
    chunks = _chunks(long_para)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_CHARS for c in chunks)
    assert " ".join(chunks).split() == long_para.split()  # nothing lost


# --- upsert / search ----------------------------------------------------------------------

def test_search_ranks_by_meaning_proxy(index):
    index.upsert(note("notes/uzay.md", "Uzay asansörü için karbon nanotüp kablo gerekir.",
                      title="Uzay asansörü"))
    index.upsert(note("notes/ekmek.md", "Ekşi maya ekmek tarifi: un, su, tuz.", title="Ekmek"))

    hits = index.search("karbon nanotüp kablo", k=2)

    assert [h.path for h in hits] == ["notes/uzay.md", "notes/ekmek.md"]
    assert hits[0].score > hits[1].score
    assert hits[0].title == "Uzay asansörü" and hits[0].type == "note"
    assert "karbon nanotüp" in hits[0].snippet


def test_one_hit_per_note_even_with_many_chunks(index):
    body = "\n\n".join(f"paragraf {i} " + "yapay zeka " * 40 for i in range(6))
    index.upsert(note("notes/long.md", body))
    index.upsert(note("notes/other.md", "yapay zeka kısa not"))

    assert index.count() > 2
    hits = index.search("yapay zeka", k=5)
    assert sorted(h.path for h in hits) == ["notes/long.md", "notes/other.md"]


def test_upsert_replaces_old_chunks_and_remove(index):
    index.upsert(note("notes/x.md", "\n\n".join(["eski içerik " * 40] * 4)))
    many = index.count()
    index.upsert(note("notes/x.md", "yeni kısa içerik"))

    assert index.count() == 1 < many
    assert "yeni kısa" in index.search("içerik", 1)[0].snippet

    index.remove("notes/x.md")
    assert index.count() == 0
    assert index.search("içerik") == []


def test_bookmark_metadata_and_header_are_indexed(index):
    index.upsert(Bookmark(title="Klavye", content="", tags=["gadget", "wishlist"],
                          path="bookmarks/k.md", url="https://x", summary="Mekanik klavye incelemesi."))
    [hit] = index.search("mekanik klavye wishlist", 1)
    assert hit.type == "bookmark"
    assert hit.path == "bookmarks/k.md"


def test_search_edge_cases(index):
    assert index.search("anything") == []  # empty index
    index.upsert(note("notes/a.md", "bir"))
    assert index.search("   ") == []
    assert index.search("bir", k=0) == []
    with pytest.raises(ValueError):
        index.upsert(note(None, "no path"))


def test_index_persists_across_instances(tmp_path):
    path = str(tmp_path / "index")
    IndexStore(embed=fake_embed, path=path).upsert(note("notes/a.md", "kalıcı not"))
    assert IndexStore(embed=fake_embed, path=path).search("kalıcı")[0].path == "notes/a.md"


# --- rebuild: the index is derived ----------------------------------------------------------

def test_rebuild_from_vault_reproduces_search(tmp_path, vault):
    index_path = tmp_path / "index"
    writer = NoteWriter(vault=vault, index=IndexStore(embed=fake_embed, path=str(index_path)))
    writer.run({"title": "Uzay asansörü", "content": "Karbon nanotüp kablo fikri."})
    writer.run({"title": "Alışveriş", "content": "Süt, yumurta, ekmek."})
    writer.save_bookmark(Bookmark(title="Makale", content="sonra oku", tags=["ai"],
                                  url="https://a", summary="Yapay zeka ajanları hakkında."))
    before = [h.path for h in writer.index.search("nanotüp kablo", 3)]

    # A brand-new, empty index stands in for "index deleted" (chromadb caches clients
    # per path within one process, so deleting the live directory is not a fair test).
    fresh = IndexStore(embed=fake_embed, path=str(tmp_path / "rebuilt-index"))
    assert fresh.search("nanotüp kablo") == []
    assert fresh.rebuild(vault) == 3

    assert [h.path for h in fresh.search("nanotüp kablo", 3)] == before
    assert fresh.search("yapay zeka ajanları", 1)[0].type == "bookmark"


def test_rebuild_skips_hidden_and_broken_notes_and_resets(index, vault, caplog):
    vault.write("notes/ok.md", "iyi not")
    vault.write(".obsidian/x.md", "gizli")
    (vault.root / "notes" / "broken.md").write_text("---\n: [bozuk yaml\n---\nx\n")
    index.upsert(note("notes/gone.md", "silinmiş not"))  # stale entry not in the vault

    assert index.rebuild(vault) == 1
    assert [h.path for h in index.search("not", 5)] == ["notes/ok.md"]
    assert "Skipping unreadable note notes/broken.md" in caplog.text


# --- NoteWriter indexing ------------------------------------------------------------------

def test_note_writer_indexes_plain_notes(vault, index):
    path = NoteWriter(vault=vault, index=index).run({"title": "Fikir", "content": "Güneş paneli"})
    [hit] = index.search("güneş paneli", 1)
    assert hit.path == path and hit.title == "Fikir"


def test_index_failure_never_loses_the_note(vault, caplog):
    class Broken:
        def upsert(self, note):
            raise RuntimeError("index down")

    path = NoteWriter(vault=vault, index=Broken()).run({"title": "x", "content": "y"})
    assert vault.exists(path)
    assert "Index upsert failed" in caplog.text


# --- Retriever ----------------------------------------------------------------------------

def test_retriever_formats_hits(index):
    index.upsert(note("notes/uzay.md", "Uzay asansörü karbon nanotüp"))
    reply = Retriever(index).run({"query": "uzay asansörü", "k": 3})
    path, score, snippet = reply.split(" · ")
    assert path == "notes/uzay.md" and 0 < float(score) <= 1 and "Uzay" in snippet


def test_retriever_empty_and_bad_input(index):
    retriever = Retriever(index)
    assert retriever.run({"query": "hiçbir şey"}) == "No matching notes."
    for bad in ({}, {"query": "  "}, {"query": "x", "k": "3"}, {"query": "x", "k": True}):
        with pytest.raises(ValueError):
            retriever.run(bad)


def test_retriever_clamps_k(index):
    for i in range(15):
        index.upsert(note(f"notes/{i}.md", f"ortak kelime {i}"))
    assert len(Retriever(index).run({"query": "ortak kelime", "k": 50}).splitlines()) == 10
    assert len(Retriever(index).run({"query": "ortak kelime", "k": -3}).splitlines()) == 1


# --- embedder (no download) ---------------------------------------------------------------

class FakeModel:
    def __init__(self) -> None:
        self.seen = []

    def encode(self, texts, **kw):
        import numpy as np

        self.seen.append((texts, kw))
        return np.ones((len(texts), 3))


def test_embedder_prefixes_only_for_e5_and_normalizes():
    plain = Embedder("paraphrase-multilingual")
    plain._model = FakeModel()
    assert plain.embed(["a"], "query") == [[1.0, 1.0, 1.0]]
    assert plain._model.seen[0][0] == ["a"]
    assert plain._model.seen[0][1]["normalize_embeddings"] is True

    e5 = Embedder("intfloat/multilingual-e5-small")
    e5._model = FakeModel()
    e5.embed(["q"], "query")
    e5.embed(["d"], "document")
    assert [s[0] for s in e5._model.seen] == [["query: q"], ["passage: d"]]

    assert Embedder()._model is None  # lazy: nothing loaded until first embed
    assert Embedder().embed([]) == []


def test_llm_client_embed_delegates(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    from second_brain.llm_client import LLMClient

    llm = LLMClient()
    llm.embedder._model = FakeModel()
    assert llm.embed(["x", "y"], "query") == [[1.0, 1.0, 1.0]] * 2


# --- config + wiring ----------------------------------------------------------------------

def test_index_path_inside_vault_is_rejected(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setattr(config_module, "ENV_FILE", "")
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("INDEX_PATH", str(vault / ".index"))
    with pytest.raises(RuntimeError, match="INDEX_PATH"):
        config_module.assert_outside_vault(str(vault))


def _build(monkeypatch, tmp_path, recall):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("INDEX_PATH", str(tmp_path / "index"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")

    class FakeLLM:
        def embed(self, texts, kind="document"):
            return fake_embed(texts, kind)

    monkeypatch.setattr(main_module, "LLMClient", FakeLLM)
    monkeypatch.setattr(main_module, "TelegramGateway", lambda **kw: None)
    monkeypatch.setitem(main_module.Config.manifest, "recall", recall)
    captured = {}
    real = main_module.Orchestrator
    monkeypatch.setattr(main_module, "Orchestrator", lambda **kw: captured.update(kw) or real(**kw))
    main_module.build()
    return {t.name: t for t in captured["tools"]}


def test_recall_on_by_default_and_off_when_disabled(monkeypatch, tmp_path):
    from tests.conftest import DEFAULT_MANIFEST

    assert DEFAULT_MANIFEST["recall"] is True
    tools = _build(monkeypatch, tmp_path, recall=False)
    assert "retriever" not in tools and tools["note_writer"].index is None


def test_recall_on_wires_index_and_retriever(monkeypatch, tmp_path):
    tools = _build(monkeypatch, tmp_path, recall=True)
    assert tools["retriever"].index is tools["note_writer"].index
    tools["note_writer"].run({"title": "Fikir", "content": "rüzgar türbini"})
    assert "rüzgar" in tools["retriever"].run({"query": "rüzgar türbini"})


# --- real model (opt-in: RUN_MODEL_TESTS=1, downloads ~470 MB once) -------------------------

@pytest.mark.skipif(os.getenv("RUN_MODEL_TESTS") != "1", reason="set RUN_MODEL_TESTS=1")
def test_real_multilingual_model_finds_turkish_paraphrase(tmp_path):
    index = IndexStore(embed=Embedder().embed, path=str(tmp_path / "index"))
    index.upsert(note("notes/uzay.md", "Uzay asansörü için karbon nanotüp kablolar yeterince güçlü olabilir."))
    index.upsert(note("notes/ekmek.md", "Ekşi mayalı ekmek için unu bir gece dinlendir."))
    index.upsert(note("notes/koşu.md", "Sabah koşusundan sonra enerjim çok yüksekti."))

    assert index.search("yörüngeye çıkan kablo fikri", 1)[0].path == "notes/uzay.md"
    assert index.search("hamur tarifi", 1)[0].path == "notes/ekmek.md"
    assert index.search("spor yaptıktan sonra dinç hissetmek", 1)[0].path == "notes/koşu.md"
