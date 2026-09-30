"""ReminderDispatcher — policy check → send or defer; end to end with the real Scheduler."""
import asyncio
from datetime import datetime, timedelta

import pytest

from second_brain import main as main_module
from second_brain.orchestration.reminder_dispatcher import (
    NO_WINDOW_RETRY,
    SEND_FAILURE_RETRY,
    ReminderDispatcher,
    compose,
)
from second_brain.proactive.policy import Policy
from second_brain.proactive.scheduler import Scheduler
from second_brain.storage.state_db import StateDB
from second_brain.tools.reminder_manager import ReminderManager

NOON = datetime(2026, 10, 1, 12, 0)
NIGHT = datetime(2026, 10, 1, 23, 30)
MORNING = datetime(2026, 10, 2, 8, 0)


@pytest.fixture
def state(tmp_path):
    with StateDB(str(tmp_path / "state.db")) as db:
        yield db


class Outbox:
    def __init__(self, error=None) -> None:
        self.sent: list[str] = []
        self.error = error

    async def __call__(self, text: str) -> None:
        if self.error:
            raise self.error
        self.sent.append(text)


def setup(state, now, busy_until=None, send=None):
    reminders = ReminderManager(state=state)
    policy = Policy(state, busy_until=busy_until)
    outbox = send or Outbox()
    dispatcher = ReminderDispatcher(reminders, policy, outbox, clock=lambda: now)
    return dispatcher, reminders, outbox


def add_due(state, *texts, when=NOON - timedelta(minutes=1)):
    return [state.add_reminder(t, when) for t in texts]


def run(dispatcher, state, now):
    asyncio.run(dispatcher(state.reminders_due(now)))


# --- compose ------------------------------------------------------------------------------

def test_compose_single_and_many(state):
    [a, b] = add_due(state, "hocaya mail at", "annemi ara")
    ra, rb = state.get_reminder(a), state.get_reminder(b)
    assert compose([ra]) == "⏰ Hatırlatma: hocaya mail at"
    assert compose([ra, rb]) == "⏰ Hatırlatmalar:\n• hocaya mail at\n• annemi ara"


# --- allowed ------------------------------------------------------------------------------

def test_allowed_sends_one_message_and_marks_all_sent(state):
    ids = add_due(state, "a", "b")
    dispatcher, _, outbox = setup(state, NOON)

    run(dispatcher, state, NOON)

    assert outbox.sent == ["⏰ Hatırlatmalar:\n• a\n• b"]
    assert [state.get_reminder(i).status for i in ids] == ["sent", "sent"]
    assert state.last_nudge() == NOON
    assert Policy(state).nudges_today(NOON) == 0  # reminders do not use the nudge quota
    assert state.reminders_due(NOON + timedelta(days=1)) == []


def test_empty_list_is_noop(state):
    dispatcher, _, outbox = setup(state, NOON)
    asyncio.run(dispatcher([]))
    assert outbox.sent == [] and state.last_nudge() is None


# --- not allowed --------------------------------------------------------------------------

def test_quiet_hours_defer_until_morning(state, caplog):
    [rid] = add_due(state, "x", when=NIGHT - timedelta(minutes=5))
    dispatcher, _, outbox = setup(state, NIGHT)

    with caplog.at_level("INFO"):
        run(dispatcher, state, NIGHT)

    assert outbox.sent == []
    r = state.get_reminder(rid)
    assert (r.status, r.due) == ("deferred", MORNING)
    assert "quiet_hours" in caplog.text
    assert state.reminders_due(NIGHT + timedelta(hours=1)) == []


def test_calendar_busy_defers_to_meeting_end(state):
    [rid] = add_due(state, "x")
    end = NOON + timedelta(minutes=45)
    dispatcher, _, outbox = setup(state, NOON, busy_until=lambda t: end if t < end else None)

    run(dispatcher, state, NOON)

    assert outbox.sent == []
    assert state.get_reminder(rid).due == end


def test_no_window_falls_back_to_thirty_minutes(state):
    [rid] = add_due(state, "x")
    dispatcher, _, _ = setup(state, NOON, busy_until=lambda t: t + timedelta(hours=1))

    run(dispatcher, state, NOON)

    r = state.get_reminder(rid)
    assert (r.status, r.due) == ("deferred", NOON + NO_WINDOW_RETRY)


def test_send_failure_defers_briefly_and_does_not_mark_sent(state, caplog):
    [rid] = add_due(state, "x")
    dispatcher, _, _ = setup(state, NOON, send=Outbox(error=ConnectionError("telegram down")))

    run(dispatcher, state, NOON)

    r = state.get_reminder(rid)
    assert (r.status, r.due) == ("deferred", NOON + SEND_FAILURE_RETRY)
    assert state.last_nudge() is None
    assert "Sending reminders failed" in caplog.text


def test_reminder_closed_mid_tick_does_not_block_others(state, caplog):
    [a, b] = add_due(state, "a", "b")
    due = state.reminders_due(NOON)
    dispatcher, reminders, outbox = setup(state, NOON)
    reminders.close(a)  # user closes it between the tick's read and the send

    asyncio.run(dispatcher(due))

    assert len(outbox.sent) == 1
    assert state.get_reminder(a).status == "closed"
    assert state.get_reminder(b).status == "sent"
    assert "Skipped reminder" in caplog.text


# --- end to end ---------------------------------------------------------------------------

def test_scheduler_sends_each_due_reminder_exactly_once(state):
    rid = state.add_reminder("gerçek döngü", datetime.now() - timedelta(minutes=1))
    outbox = Outbox()
    reminders = ReminderManager(state=state)
    policy = Policy(state, quiet_hours="03:00-03:01")  # keep the test independent of the clock
    dispatcher = ReminderDispatcher(reminders, policy, outbox)

    async def scenario():
        sched = Scheduler(state, on_due=dispatcher, interval_s=0.05)
        sched.start()
        await asyncio.sleep(0.4)  # ~8 ticks
        sched.shutdown()

    asyncio.run(scenario())

    assert outbox.sent == ["⏰ Hatırlatma: gerçek döngü"]
    assert state.get_reminder(rid).status == "sent"


# --- wiring -------------------------------------------------------------------------------

def test_proactive_is_on_by_default():
    assert main_module.Config.enabled("proactive") is True


def test_build_wires_dispatcher_into_scheduler(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")
    monkeypatch.setattr(main_module, "LLMClient", lambda: object())

    sent = []

    class FakeGateway:
        def __init__(self, **kw) -> None:
            self.kw = kw

        async def send_message(self, text):
            sent.append(text)

    captured = {}
    real_scheduler = main_module.Scheduler
    monkeypatch.setattr(main_module, "TelegramGateway", FakeGateway)
    monkeypatch.setattr(
        main_module, "Scheduler", lambda **kw: captured.update(kw) or real_scheduler(**kw)
    )

    main_module.build()

    dispatcher = captured["on_due"]
    assert isinstance(dispatcher, ReminderDispatcher)
    # The injected send reaches the gateway.
    asyncio.run(dispatcher.send("merhaba"))
    assert sent == ["merhaba"]
    dispatcher.reminders.state.close()
