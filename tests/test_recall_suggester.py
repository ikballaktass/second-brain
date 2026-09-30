"""'Reminds you of' suggestions — rules, cooldown, orchestrator wiring, end to end."""
import os
import re
from datetime import datetime, timedelta

import pytest

from second_brain import main as main_module
from second_brain.llm_client import LLMResult, ToolCall
from second_brain.models import Intent
from second_brain.orchestration.context_builder import Context, ContextBuilder, RelatedNote
from second_brain.orchestration.orchestrator import Orchestrator
from second_brain.orchestration.recall_suggester import RecallSuggester, humanize_age
from second_brain.storage.index_store import IndexStore
from second_brain.storage.state_db import StateDB
from second_brain.storage.vault_repository import VaultRepository
from second_brain.tools.note_writer import NoteWriter

NOW = datetime(2026, 10, 1, 12, 0)


def rel(path="notes/uzay.md", score=0.7, days_old=21, title="Uzay asansörü"):
    created = None if days_old is None else NOW - timedelta(days=days_old)
    return RelatedNote(path, title, score, "excerpt", created=created)


def ctx(*notes):
    return Context(list(notes))


@pytest.fixture
def state(tmp_path):
    with StateDB(str(tmp_path / "state.db")) as db:
        yield db


# --- rules --------------------------------------------------------------------------------

def test_suggests_old_strong_match_with_path_and_age():
    line = RecallSuggester().suggest(ctx(rel()), Intent.CAPTURE, "✓ Kaydedildi", NOW)
    assert line == '💡 Bu sana şunu hatırlatıyor: "Uzay asansörü" — notes/uzay.md (3 hafta önce)'


@pytest.mark.parametrize("intent, expected", [
    (Intent.CAPTURE, True), (Intent.JOURNAL, True), (None, True),
    (Intent.QUERY, False), (Intent.REMINDER, False), (Intent.TASK, False),
    (Intent.STATE, False), (Intent.COMMAND, False),
])
def test_only_capture_and_journal(intent, expected):
    assert (RecallSuggester().suggest(ctx(rel()), intent, "ok", NOW) is not None) is expected


def test_score_threshold_is_stricter_than_context():
    assert RecallSuggester().suggest(ctx(rel(score=0.54)), Intent.CAPTURE, "ok", NOW) is None
    assert RecallSuggester().suggest(ctx(rel(score=0.55)), Intent.CAPTURE, "ok", NOW)


def test_note_must_be_at_least_seven_days_old():
    s = RecallSuggester()
    assert s.suggest(ctx(rel(days_old=6)), Intent.CAPTURE, "ok", NOW) is None
    assert s.suggest(ctx(rel(days_old=None)), Intent.CAPTURE, "ok", NOW) is None
    assert s.suggest(ctx(rel(days_old=7)), Intent.CAPTURE, "ok", NOW) is not None


def test_not_repeated_when_reply_already_cites_the_path():
    assert RecallSuggester().suggest(ctx(rel()), Intent.CAPTURE, "Bkz. notes/uzay.md", NOW) is None


def test_similar_title_in_reply_does_not_hide_the_older_note():
    reply = "00-Gelen/2026-10-01 Uzay asansörü v2.md"
    assert RecallSuggester().suggest(ctx(rel()), Intent.CAPTURE, reply, NOW) is not None


def test_picks_best_eligible_candidate():
    notes = ctx(rel("notes/new.md", 0.9, days_old=1, title="Yeni"),
                rel("notes/b.md", 0.6, title="B"),
                rel("notes/a.md", 0.7, title="A"))
    line = RecallSuggester().suggest(notes, Intent.JOURNAL, "ok", NOW)
    assert '"A" — notes/a.md' in line


def test_no_context_means_no_suggestion():
    assert RecallSuggester().suggest(None, Intent.CAPTURE, "ok", NOW) is None
    assert RecallSuggester().suggest(Context(), Intent.CAPTURE, "ok", NOW) is None


# --- cooldown: once a week per note -------------------------------------------------------

def test_same_note_at_most_once_a_week_and_falls_through_to_next(state):
    s = RecallSuggester(state=state)
    two = ctx(rel("notes/a.md", 0.8, title="A"), rel("notes/b.md", 0.7, title="B"))

    assert '"A"' in s.suggest(two, Intent.CAPTURE, "ok", NOW)
    assert '"B"' in s.suggest(two, Intent.CAPTURE, "ok", NOW + timedelta(days=1))
    assert s.suggest(two, Intent.CAPTURE, "ok", NOW + timedelta(days=6)) is None
    assert '"A"' in s.suggest(two, Intent.CAPTURE, "ok", NOW + timedelta(days=7))


def test_cooldown_survives_restart(tmp_path):
    path = str(tmp_path / "state.db")
    with StateDB(path) as db:
        RecallSuggester(state=db).suggest(ctx(rel()), Intent.CAPTURE, "ok", NOW)
    with StateDB(path) as db:
        s = RecallSuggester(state=db)
        assert s.suggest(ctx(rel()), Intent.CAPTURE, "ok", NOW + timedelta(days=3)) is None
        assert db.get_meta("recall_suggested:notes/uzay.md") == NOW.isoformat(timespec="seconds")


def test_in_memory_cooldown_without_state():
    s = RecallSuggester()
    assert s.suggest(ctx(rel()), Intent.CAPTURE, "ok", NOW)
    assert s.suggest(ctx(rel()), Intent.CAPTURE, "ok", NOW + timedelta(days=2)) is None


@pytest.mark.parametrize("days, text", [
    (7, "7 gün önce"), (13, "13 gün önce"), (14, "2 hafta önce"), (59, "8 hafta önce"),
    (60, "2 ay önce"), (364, "12 ay önce"), (365, "1 yıl önce"), (800, "2 yıl önce"),
])
def test_humanize_age(days, text):
    assert humanize_age(timedelta(days=days, hours=5)) == text


def test_title_falls_back_to_file_name():
    line = RecallSuggester().suggest(ctx(rel("notes/eski fikir.md", title="")),
                                     Intent.CAPTURE, "ok", NOW)
    assert '"eski fikir" — notes/eski fikir.md' in line


# --- orchestrator -------------------------------------------------------------------------

class LLM:
    def __init__(self, result) -> None:
        self.result = result

    def complete(self, prompt, tools=None, system=None, model=None, max_tokens=1024):
        return self.result


class Router:
    def __init__(self, intent) -> None:
        self.intent = intent

    def classify(self, message):
        return self.intent


class StaticContext:
    def __init__(self, context) -> None:
        self.context = context

    def build(self, message):
        return self.context


def test_orchestrator_appends_hint_after_text_reply():
    orch = Orchestrator(LLM(LLMResult(text="Not aldım.")), Router(Intent.CAPTURE),
                        StaticContext(ctx(rel(days_old=30))), [], suggester=FixedClock())
    reply = orch.handle("uzay asansörü için yeni fikir")
    assert reply.startswith("Not aldım.\n\n💡 Bu sana şunu hatırlatıyor:")


def test_orchestrator_appends_hint_after_tool_receipts():
    class Tool:
        name = "note_writer"

        def to_api(self):
            return {"name": self.name}

        def run(self, args):
            return "00-Gelen/yeni.md"

    llm = LLM(LLMResult(text="", tool_calls=[ToolCall("1", "note_writer", {})]))
    orch = Orchestrator(llm, Router(Intent.CAPTURE), StaticContext(ctx(rel())), [Tool()],
                        suggester=FixedClock())
    assert orch.handle("fikir").splitlines()[0] == "00-Gelen/yeni.md"


def test_orchestrator_without_suggester_or_on_failure(caplog):
    plain = Orchestrator(LLM(LLMResult(text="ok")), Router(Intent.CAPTURE),
                         StaticContext(ctx(rel())), [])
    assert plain.handle("x y z") == "ok"

    class Broken:
        def suggest(self, *a, **k):
            raise RuntimeError("boom")

    broken = Orchestrator(LLM(LLMResult(text="ok")), None, StaticContext(ctx(rel())), [],
                          suggester=Broken())
    assert broken.handle("x y z") == "ok"
    assert "Recall suggestion failed" in caplog.text


class FixedClock(RecallSuggester):
    def suggest(self, context, intent, reply, now=None):
        return super().suggest(context, intent, reply, now or NOW)


# --- end to end: old note suggested, the note just saved is not ----------------------------

def test_end_to_end_old_note_is_suggested_new_one_is_not(tmp_path):
    from tests.test_index_store import fake_embed

    vault = VaultRepository(str(tmp_path / "vault"))
    index = IndexStore(embed=fake_embed, path=str(tmp_path / "index"))
    writer = NoteWriter(vault=vault, index=index)
    old_path = writer.run({"title": "Uzay asansörü", "content": "karbon nanotüp kablo uzay asansörü"})
    old = vault.root / old_path
    month_ago = (datetime.now() - timedelta(days=30)).timestamp()
    os.utime(old, (month_ago, month_ago))
    # frontmatter `created` wins over mtime, so age it there too
    month_ago_iso = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    old.write_text(re.sub(r"^created: .*$", f"created: '{month_ago_iso}'", old.read_text(),
                          flags=re.M))

    class SavingLLM:
        def complete(self, prompt, tools=None, system=None, model=None, max_tokens=1024):
            return LLMResult(text="", tool_calls=[ToolCall(
                "1", "note_writer",
                {"title": "Uzay asansörü v2", "content": "karbon nanotüp kablo uzay asansörü yeni"},
            )])

    orch = Orchestrator(SavingLLM(), Router(Intent.CAPTURE),
                        ContextBuilder(index, vault, min_score=0.3), [writer],
                        suggester=RecallSuggester())
    reply = orch.handle("karbon nanotüp kablo uzay asansörü yeni")

    new_path, hint = reply.split("\n\n")
    assert new_path.endswith("Uzay asansörü v2.md")
    assert hint.startswith('💡 Bu sana şunu hatırlatıyor: "Uzay asansörü" — ' + old_path)
    assert "hafta önce" in hint or "ay önce" in hint


# --- wiring -------------------------------------------------------------------------------

def _captured(monkeypatch, tmp_path, recall, proactive=True):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")

    class FakeLLM:
        def embed(self, texts, kind="document"):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(main_module, "LLMClient", FakeLLM)
    monkeypatch.setattr(main_module, "TelegramGateway", lambda **kw: None)
    monkeypatch.setitem(main_module.Config.manifest, "recall", recall)
    monkeypatch.setitem(main_module.Config.manifest, "proactive", proactive)
    captured = {}
    real = main_module.Orchestrator
    monkeypatch.setattr(main_module, "Orchestrator", lambda **kw: captured.update(kw) or real(**kw))
    main_module.build()
    return captured


def test_build_wires_suggester_with_state_db(monkeypatch, tmp_path):
    captured = _captured(monkeypatch, tmp_path, recall=True)
    suggester = captured["suggester"]
    assert isinstance(suggester, RecallSuggester)
    reminders = next(t for t in captured["tools"] if t.name == "reminder_manager")
    assert suggester.state is reminders.state


def test_build_suggester_in_memory_without_proactive(monkeypatch, tmp_path):
    captured = _captured(monkeypatch, tmp_path, recall=True, proactive=False)
    assert captured["suggester"].state is None


def test_build_without_recall_has_no_suggester(monkeypatch, tmp_path):
    assert _captured(monkeypatch, tmp_path, recall=False)["suggester"] is None
