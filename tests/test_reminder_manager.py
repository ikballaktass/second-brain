"""ReminderManager — create/list/close + lifecycle on a real temp StateDB."""
import re
from datetime import datetime, timedelta, timezone

import pytest

from second_brain import main as main_module
from second_brain.llm_client import LLMResult
from second_brain.orchestration.orchestrator import Orchestrator, system_prompt
from second_brain.storage.state_db import StateDB
from second_brain.tools.reminder_manager import ALLOWED_TRANSITIONS, ReminderManager

FUTURE = datetime.now().replace(microsecond=0) + timedelta(days=1)


@pytest.fixture
def state(tmp_path):
    with StateDB(str(tmp_path / "state.db")) as db:
        yield db


@pytest.fixture
def rm(state):
    return ReminderManager(state=state)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


# --- create -------------------------------------------------------------------------------

def test_create_persists_pending_reminder(rm, state):
    reply = rm.run({"action": "create", "text": "hocaya mail at", "due": iso(FUTURE)})

    [reminder] = state.list_reminders()
    assert reminder.text == "hocaya mail at"
    assert reminder.due == FUTURE.replace(second=0)
    assert reminder.status == "pending"
    assert reply == f"Reminder set → #{reminder.id} · {FUTURE:%Y-%m-%d %H:%M} · hocaya mail at (pending)"


def test_create_converts_aware_due_to_local(rm, state):
    due_utc = (FUTURE.astimezone(timezone.utc)).isoformat().replace("+00:00", "Z")

    rm.run({"action": "create", "text": "x", "due": due_utc})

    assert state.list_reminders()[0].due == FUTURE


@pytest.mark.parametrize("args, message", [
    ({"action": "create", "due": iso(FUTURE)}, "'text' is required"),
    ({"action": "create", "text": "  ", "due": iso(FUTURE)}, "'text' is required"),
    ({"action": "create", "text": "x"}, "'due' must be"),
    ({"action": "create", "text": "x", "due": "yarın 10'da"}, "not an ISO datetime"),
    ({"action": "create", "text": "x", "due": iso(datetime.now() - timedelta(hours=1))},
     "in the past"),
    ({"action": "delete"}, "unknown action"),
    ({}, "unknown action"),
])
def test_invalid_input_raises_value_error(rm, args, message):
    with pytest.raises(ValueError, match=message):
        rm.run(args)


def test_past_tolerance_allows_just_now(rm):
    now = datetime(2026, 10, 1, 10, 0, 30)
    reminder = rm.create("şimdi", datetime(2026, 10, 1, 10, 0), now=now)
    assert reminder.status == "pending"


# --- list ---------------------------------------------------------------------------------

def test_list_open_reminders_sorted(rm):
    later = rm.create("sonra", FUTURE + timedelta(hours=2))
    first = rm.create("önce", FUTURE)
    done = rm.create("bitti", FUTURE + timedelta(hours=1))
    rm.close(done.id)

    assert rm.run({"action": "list"}).splitlines() == [
        f"#{first.id} · {FUTURE:%Y-%m-%d %H:%M} · önce (pending)",
        f"#{later.id} · {FUTURE + timedelta(hours=2):%Y-%m-%d %H:%M} · sonra (pending)",
    ]


def test_list_when_empty(rm):
    assert rm.run({"action": "list"}) == "No open reminders."


# --- close --------------------------------------------------------------------------------

def test_close_by_id(rm):
    r = rm.create("fatura", FUTURE)
    reply = rm.run({"action": "close", "id": r.id})
    assert reply.startswith(f"Reminder closed → #{r.id}")
    assert reply.endswith("(closed)")


def test_close_by_unique_query_is_case_insensitive_and_turkish_aware(rm, state):
    target = rm.create("İstanbul biletini al", FUTURE)
    rm.create("annemi ara", FUTURE)

    rm.run({"action": "close", "query": "istanbul"})

    assert state.get_reminder(target.id).status == "closed"


def test_close_by_query_with_no_or_many_matches(rm):
    a = rm.create("mail at hocaya", FUTURE)
    b = rm.create("mail at annene", FUTURE)

    with pytest.raises(ValueError, match="no open reminder matches"):
        rm.run({"action": "close", "query": "fatura"})
    with pytest.raises(ValueError, match=r"2 reminders match") as err:
        rm.run({"action": "close", "query": "mail"})
    assert f"#{a.id}" in str(err.value) and f"#{b.id}" in str(err.value)


def test_close_ignores_already_closed_in_query(rm):
    old = rm.create("mail at", FUTURE)
    rm.close(old.id)
    new = rm.create("mail at tekrar", FUTURE)

    rm.run({"action": "close", "query": "mail"})

    assert rm.state.get_reminder(new.id).status == "closed"


@pytest.mark.parametrize("args, message", [
    ({"action": "close"}, "give an 'id' or a 'query'"),
    ({"action": "close", "id": "3"}, "'id' must be an integer"),
    ({"action": "close", "id": True}, "'id' must be an integer"),
    ({"action": "close", "id": 999}, "not found"),
])
def test_close_bad_input(rm, args, message):
    with pytest.raises(ValueError, match=message):
        rm.run(args)


# --- lifecycle ----------------------------------------------------------------------------

def test_full_lifecycle_pending_deferred_sent_closed(rm):
    r = rm.create("toplantı", FUTURE)
    assert [x.id for x in rm.due(now=FUTURE)] == [r.id]

    deferred = rm.defer(r.id, FUTURE + timedelta(hours=1))
    assert deferred.status == "deferred"
    assert rm.due(now=FUTURE) == []
    assert [x.id for x in rm.due(now=FUTURE + timedelta(hours=1))] == [r.id]

    assert rm.mark_sent(r.id).status == "sent"
    assert rm.due(now=FUTURE + timedelta(days=9)) == []
    assert rm.close(r.id).status == "closed"


@pytest.mark.parametrize("start, target", [
    (start, target)
    for start in ALLOWED_TRANSITIONS
    for target in ("sent", "deferred", "closed")
    if target not in ALLOWED_TRANSITIONS[start]
])
def test_forbidden_transitions(rm, state, start, target):
    r = rm.create("x", FUTURE)
    state.set_reminder_status(r.id, start)

    with pytest.raises(ValueError, match="cannot become"):
        if target == "deferred":
            rm.defer(r.id, FUTURE)
        else:
            rm._transition(r.id, target)


def test_forbidden_transition_leaves_row_untouched(rm):
    r = rm.create("x", FUTURE)
    rm.close(r.id)
    with pytest.raises(ValueError):
        rm.defer(r.id, FUTURE + timedelta(days=1))
    after = rm.state.get_reminder(r.id)
    assert (after.status, after.due) == ("closed", FUTURE)


def test_to_api_schema(rm):
    api = rm.to_api()
    assert api["name"] == "reminder_manager"
    assert api["input_schema"]["properties"]["action"]["enum"] == ["create", "list", "close"]


# --- orchestrator + wiring ----------------------------------------------------------------

def test_system_prompt_contains_current_time():
    prompt = system_prompt(datetime(2026, 10, 2, 18, 5))
    assert "Current local time: 2026-10-02 18:05 (Friday)." in prompt
    assert "reminder_manager" in prompt


def test_orchestrator_sends_current_time_to_llm():
    seen = {}

    class LLM:
        def complete(self, prompt, tools=None, system=None, model=None, max_tokens=1024):
            seen["system"] = system
            return LLMResult(text="ok")

    Orchestrator(llm=LLM(), router=None, context=None, tools=[]).handle("merhaba")

    assert re.search(r"Current local time: \d{4}-\d{2}-\d{2} \d{2}:\d{2} \(\w+\)\.", seen["system"])


def _build_tools(monkeypatch, tmp_path, proactive):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")
    monkeypatch.setattr(main_module, "LLMClient", lambda: object())
    monkeypatch.setattr(main_module, "TelegramGateway", lambda **kw: None)
    monkeypatch.setitem(main_module.Config.manifest, "proactive", proactive)
    captured = {}
    real = main_module.Orchestrator
    monkeypatch.setattr(main_module, "Orchestrator", lambda **kw: captured.update(kw) or real(**kw))
    main_module.build()
    return {t.name: t for t in captured["tools"]}


def test_reminder_manager_not_registered_when_proactive_off(monkeypatch, tmp_path):
    tools = _build_tools(monkeypatch, tmp_path, proactive=False)
    assert "reminder_manager" not in tools
    assert not (tmp_path / "state.db").exists()


def test_reminder_manager_registered_when_proactive_on(monkeypatch, tmp_path):
    tools = _build_tools(monkeypatch, tmp_path, proactive=True)
    assert isinstance(tools["reminder_manager"], ReminderManager)
    assert tools["reminder_manager"].state.path == str(tmp_path / "state.db")
    tools["reminder_manager"].state.close()
