"""CalendarTool — parsing, busy_until, cache, run(), from_config. Fake Google service; no network."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from second_brain import main as main_module
from second_brain.proactive.policy import Policy
from second_brain.storage.state_db import StateDB
from second_brain.tools.calendar import CACHE_TTL_S, CalendarEvent, CalendarTool, _parse_event

DAY = date(2026, 10, 1)


def local_iso(hh, mm=0, day=DAY) -> str:
    """RFC3339 with the machine's own offset, like Google returns for the user's zone."""
    return datetime(day.year, day.month, day.day, hh, mm).astimezone().isoformat()


def at(hh, mm=0) -> datetime:
    return datetime(DAY.year, DAY.month, DAY.day, hh, mm)


def timed(summary, start, end, **extra):
    return {"summary": summary, "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


class FakeService:
    def __init__(self, items=None, error=None) -> None:
        self.items = items or []
        self.error = error
        self.calls = []

    def events(self):
        return self

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        if self.error:
            raise self.error
        return {"items": self.items}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self):
        return self.now


def tool(items=None, error=None, clock=None):
    service = FakeService(items, error)
    return CalendarTool(service, clock=clock or Clock(), today=lambda: DAY), service


# --- parsing ------------------------------------------------------------------------------

def test_parse_timed_event_to_naive_local():
    event = _parse_event(timed("Ekonometri", local_iso(9), local_iso(10, 30)))
    assert event == CalendarEvent(at(9), at(10, 30), "Ekonometri", all_day=False, busy=True)


def test_parse_utc_event_is_converted_to_local():
    start_utc = at(9).astimezone().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = at(10).astimezone().astimezone(timezone.utc).isoformat()
    event = _parse_event(timed("x", start_utc, end_utc))
    assert (event.start, event.end) == (at(9), at(10))


def test_parse_all_day_free_declined_cancelled_untitled():
    all_day = _parse_event({"summary": "Doğum günü", "start": {"date": "2026-10-01"},
                            "end": {"date": "2026-10-02"}})
    assert all_day.all_day and all_day.start == at(0) and all_day.end == at(0) + timedelta(days=1)

    free = _parse_event(timed("Odak", local_iso(9), local_iso(10), transparency="transparent"))
    assert free.busy is False

    declined = _parse_event(timed("Toplantı", local_iso(9), local_iso(10), attendees=[
        {"email": "a@x", "responseStatus": "accepted"},
        {"email": "me@x", "self": True, "responseStatus": "declined"},
    ]))
    assert declined.busy is False

    assert _parse_event(timed("x", local_iso(9), local_iso(10), status="cancelled")) is None
    assert _parse_event({"start": {"dateTime": local_iso(9)},
                         "end": {"dateTime": local_iso(10)}}).summary == "(no title)"


# --- events_on + cache --------------------------------------------------------------------

def test_events_on_queries_the_local_day_and_sorts():
    cal, service = tool([timed("b", local_iso(14), local_iso(15)),
                         timed("a", local_iso(9), local_iso(10))])

    events = cal.events_on(DAY)

    assert [e.summary for e in events] == ["a", "b"]
    [call] = service.calls
    assert call["calendarId"] == "primary"
    assert call["singleEvents"] is True and call["orderBy"] == "startTime"
    assert datetime.fromisoformat(call["timeMin"]).replace(tzinfo=None) == at(0)
    assert datetime.fromisoformat(call["timeMax"]).replace(tzinfo=None) == at(0) + timedelta(days=1)


def test_cache_expires_after_ttl():
    clock = Clock()
    cal, service = tool([], clock=clock)

    cal.events_on(DAY)
    cal.events_on(DAY)
    assert len(service.calls) == 1

    clock.now += CACHE_TTL_S
    cal.events_on(DAY)
    assert len(service.calls) == 2

    cal.events_on(DAY + timedelta(days=1))  # another day is another cache entry
    assert len(service.calls) == 3


# --- busy_until ---------------------------------------------------------------------------

def test_busy_until_inside_outside_and_boundaries():
    cal, _ = tool([timed("Ders", local_iso(9), local_iso(10))])
    assert cal.busy_until(at(9)) == at(10)
    assert cal.busy_until(at(9, 30)) == at(10)
    assert cal.busy_until(at(10)) is None
    assert cal.busy_until(at(8, 59)) is None


def test_busy_until_overlapping_takes_latest_end():
    cal, _ = tool([timed("a", local_iso(9), local_iso(10)), timed("b", local_iso(9, 30), local_iso(11))])
    assert cal.busy_until(at(9, 45)) == at(11)


def test_all_day_free_and_declined_never_block():
    cal, _ = tool([
        {"summary": "Tatil", "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-02"}},
        timed("Odak", local_iso(9), local_iso(12), transparency="transparent"),
        timed("Reddedildi", local_iso(9), local_iso(12),
              attendees=[{"self": True, "responseStatus": "declined"}]),
    ])
    assert cal.busy_until(at(10)) is None


def test_busy_until_errors_propagate_and_policy_treats_as_free(tmp_path, caplog):
    cal, _ = tool(error=ConnectionError("google down"))
    with pytest.raises(ConnectionError):
        cal.busy_until(at(10))
    with StateDB(str(tmp_path / "s.db")) as db:
        assert Policy(db, busy_until=cal.busy_until).decide(at(10), "reminder").allowed
    assert "treating the user as free" in caplog.text


def test_policy_defers_reminder_until_meeting_ends(tmp_path):
    cal, _ = tool([timed("Toplantı", local_iso(10), local_iso(11, 15))])
    with StateDB(str(tmp_path / "s.db")) as db:
        decision = Policy(db, busy_until=cal.busy_until).decide(at(10, 20), "reminder")
    assert (decision.allowed, decision.reason, decision.retry_at) == (
        False, "calendar_busy", at(11, 15)
    )


# --- run ----------------------------------------------------------------------------------

def test_run_lists_day():
    cal, service = tool([
        timed("Ekonometri", local_iso(9), local_iso(10, 30)),
        {"summary": "Doğum günü", "start": {"date": "2026-10-01"}, "end": {"date": "2026-10-02"}},
        timed("Odak", local_iso(14), local_iso(15), transparency="transparent"),
    ])
    assert cal.run({"action": "list"}).splitlines() == [
        "2026-10-01:",
        "all day · Doğum günü",
        "09:00–10:30 · Ekonometri",
        "14:00–15:00 · Odak (free)",
    ]


def test_run_with_date_and_empty_day():
    cal, service = tool([])
    assert cal.run({"action": "list", "date": "2026-10-05"}) == "No events on 2026-10-05."
    assert datetime.fromisoformat(service.calls[0]["timeMin"]).date() == date(2026, 10, 5)


@pytest.mark.parametrize("args, message", [
    ({"action": "create"}, "unknown action"),
    ({}, "unknown action"),
    ({"action": "list", "date": "yarın"}, "must be YYYY-MM-DD"),
])
def test_run_bad_input(args, message):
    cal, _ = tool([])
    with pytest.raises(ValueError, match=message):
        cal.run(args)


def test_run_api_failure_becomes_value_error():
    cal, _ = tool(error=OSError("network"))
    with pytest.raises(ValueError, match="calendar is unavailable right now"):
        cal.run({"action": "list"})


# --- from_config + wiring -----------------------------------------------------------------

def test_from_config_without_token_fails_with_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(RuntimeError, match="scripts/google_auth.py"):
        CalendarTool.from_config()


def test_from_config_with_unrefreshable_token_fails(monkeypatch, tmp_path):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({
        "token": "x", "refresh_token": "", "client_id": "c", "client_secret": "s",
        "expiry": "2000-01-01T00:00:00Z",
    }))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(token))
    with pytest.raises((RuntimeError, ValueError)):
        CalendarTool.from_config()


def test_from_config_builds_client_offline(monkeypatch, tmp_path):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({
        "token": "x", "refresh_token": "r", "client_id": "c", "client_secret": "s",
    }))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(token))
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "work@example.com")

    cal = CalendarTool.from_config()

    assert cal.calendar_id == "work@example.com"
    assert hasattr(cal.service, "events")


def _build(monkeypatch, tmp_path, calendar_on, fake_calendar=None):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")
    monkeypatch.setattr(main_module, "LLMClient", lambda: object())
    monkeypatch.setattr(main_module, "TelegramGateway", lambda **kw: None)
    monkeypatch.setitem(main_module.Config.manifest, "calendar", calendar_on)
    if fake_calendar is not None:
        monkeypatch.setattr(main_module.CalendarTool, "from_config", classmethod(
            lambda cls: fake_calendar))
    captured = {}
    real_orch, real_policy = main_module.Orchestrator, main_module.Policy.from_config
    monkeypatch.setattr(main_module, "Orchestrator",
                        lambda **kw: captured.setdefault("orch", kw) and real_orch(**kw))
    monkeypatch.setattr(main_module.Policy, "from_config", staticmethod(
        lambda state, busy_until=None, state_flags=None: captured.setdefault(
            "busy_until", busy_until) or real_policy(state, busy_until=busy_until)))
    main_module.build()
    return captured


def test_calendar_on_by_default_and_off_when_disabled(monkeypatch, tmp_path):
    from tests.conftest import DEFAULT_MANIFEST

    assert DEFAULT_MANIFEST["calendar"] is True
    captured = _build(monkeypatch, tmp_path, calendar_on=False)
    assert "calendar" not in {t.name for t in captured["orch"]["tools"]}
    assert captured["busy_until"] is None


def test_calendar_on_registers_tool_and_feeds_policy(monkeypatch, tmp_path):
    cal, _ = tool([])
    captured = _build(monkeypatch, tmp_path, calendar_on=True, fake_calendar=cal)
    assert cal in captured["orch"]["tools"]
    assert captured["busy_until"] == cal.busy_until


def test_calendar_on_without_token_stops_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(RuntimeError, match="Google token not found"):
        _build(monkeypatch, tmp_path, calendar_on=True)

