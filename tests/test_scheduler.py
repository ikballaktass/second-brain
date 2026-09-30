"""Scheduler — tick logic, job registration, and a real run inside an asyncio loop."""
import asyncio
from datetime import datetime, timedelta

import pytest
from apscheduler.triggers.interval import IntervalTrigger
from telegram.ext import Application

from second_brain import main as main_module
from second_brain.interface.telegram_gateway import TelegramGateway
from second_brain.proactive.scheduler import REMINDER_JOB, Scheduler
from second_brain.storage.state_db import StateDB

T0 = datetime(2026, 10, 1, 9, 0)


@pytest.fixture
def state(tmp_path):
    with StateDB(str(tmp_path / "state.db")) as db:
        yield db


class Recorder:
    def __init__(self, error=None) -> None:
        self.calls = []
        self.error = error

    async def __call__(self, reminders):
        self.calls.append([r.id for r in reminders])
        if self.error:
            raise self.error


# --- tick ---------------------------------------------------------------------------------

def test_tick_hands_only_due_reminders_to_handler(state):
    due = state.add_reminder("şimdi", T0)
    state.add_reminder("sonra", T0 + timedelta(hours=1))
    handler = Recorder()

    result = asyncio.run(Scheduler(state, on_due=handler).tick(now=T0))

    assert [r.id for r in result] == [due]
    assert handler.calls == [[due]]


def test_tick_with_nothing_due_does_not_call_handler(state):
    state.add_reminder("sonra", T0 + timedelta(hours=1))
    handler = Recorder()

    assert asyncio.run(Scheduler(state, on_due=handler).tick(now=T0)) == []
    assert handler.calls == []


def test_tick_without_handler_only_logs(state, caplog):
    state.add_reminder("x", T0)
    with caplog.at_level("INFO"):
        result = asyncio.run(Scheduler(state).tick(now=T0))
    assert len(result) == 1
    assert "no handler is wired" in caplog.text


def test_tick_survives_handler_error(state, caplog):
    state.add_reminder("x", T0)
    handler = Recorder(error=RuntimeError("boom"))

    result = asyncio.run(Scheduler(state, on_due=handler).tick(now=T0))

    assert len(result) == 1
    assert "handler failed" in caplog.text


def test_tick_survives_storage_error(state, caplog):
    state.close()  # any query now raises sqlite3.ProgrammingError
    assert asyncio.run(Scheduler(state, on_due=Recorder()).tick(now=T0)) == []
    assert "Could not read due reminders" in caplog.text


def test_invalid_interval_rejected(state):
    with pytest.raises(ValueError):
        Scheduler(state, interval_s=0)


# --- job registration ---------------------------------------------------------------------

def test_start_registers_single_reminder_job(state):
    async def scenario():
        sched = Scheduler(state, interval_s=30)
        sched.start()
        sched.start()  # idempotent
        jobs = sched._scheduler.get_jobs()
        sched.shutdown()
        return sched, jobs

    sched, jobs = asyncio.run(scenario())

    assert [j.id for j in jobs] == [REMINDER_JOB]
    assert jobs[0].max_instances == 1 and jobs[0].coalesce is True
    assert jobs[0].trigger.interval == timedelta(seconds=30)
    assert [(j.kind, j.last_run) for j in state.jobs()] == [(REMINDER_JOB, None)]
    assert not sched.running


def test_add_job_rejects_unknown_kind(state):
    async def noop():
        pass

    with pytest.raises(ValueError):
        Scheduler(state).add_job("cleanup", noop, IntervalTrigger(seconds=5))


# --- real loop ----------------------------------------------------------------------------

def test_runs_inside_event_loop_and_records_last_run(state):
    due = state.add_reminder("geçmiş", datetime.now() - timedelta(minutes=5))
    handler = Recorder()

    async def scenario():
        sched = Scheduler(state, on_due=handler, interval_s=0.05)
        sched.start()
        await asyncio.sleep(0.4)
        sched.shutdown()

    asyncio.run(scenario())

    # run_now fires immediately, then every 50 ms; the handler does not
    # transition the reminder, so it keeps being handed over (documented contract).
    assert len(handler.calls) >= 2
    assert all(call == [due] for call in handler.calls)
    assert state.jobs()[0].last_run is not None


def test_failing_job_still_records_last_run(state):
    async def broken():
        raise RuntimeError("boom")

    async def scenario():
        sched = Scheduler(state)
        sched.add_job("analyze", broken, IntervalTrigger(seconds=60), run_now=True)
        sched._scheduler.start()
        await asyncio.sleep(0.2)
        sched.shutdown()

    asyncio.run(scenario())

    [job] = state.jobs()
    assert job.kind == "analyze" and job.last_run is not None


# --- gateway hooks + wiring ---------------------------------------------------------------

def test_gateway_hooks_run_on_post_init_and_shutdown():
    events = []

    async def up():
        events.append("up")

    async def down():
        events.append("down")

    app = Application.builder().token("123:abc").build()
    gw = TelegramGateway("123:abc", 1, message_handler=None, application=app,
                         on_startup=up, on_shutdown=down)

    asyncio.run(app.post_init(app))
    asyncio.run(app.post_shutdown(app))

    assert events == ["up", "down"]


def test_gateway_without_hooks_is_fine():
    app = Application.builder().token("123:abc").build()
    TelegramGateway("123:abc", 1, message_handler=None, application=app)
    asyncio.run(app.post_init(app))
    asyncio.run(app.post_shutdown(app))


def _build_gateway_kwargs(monkeypatch, tmp_path, proactive):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("SCHEDULER_INTERVAL_S", "15")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")
    monkeypatch.setattr(main_module, "LLMClient", lambda: object())
    monkeypatch.setitem(main_module.Config.manifest, "proactive", proactive)
    captured = {}
    monkeypatch.setattr(main_module, "TelegramGateway", lambda **kw: captured.update(kw))
    main_module.build()
    return captured


def test_build_without_proactive_has_no_hooks(monkeypatch, tmp_path):
    kwargs = _build_gateway_kwargs(monkeypatch, tmp_path, proactive=False)
    assert kwargs["on_startup"] is None and kwargs["on_shutdown"] is None


def test_build_with_proactive_starts_and_stops_scheduler(monkeypatch, tmp_path):
    kwargs = _build_gateway_kwargs(monkeypatch, tmp_path, proactive=True)

    async def lifecycle():  # PTB runs post_init and post_shutdown on the same loop
        await kwargs["on_startup"]()
        await asyncio.sleep(0.1)
        await kwargs["on_shutdown"]()

    asyncio.run(lifecycle())

    # start() registered the reminder tick with the configured interval.
    with StateDB(str(tmp_path / "state.db")) as db:
        assert (REMINDER_JOB, "interval[0:00:15]") in [(j.kind, j.schedule) for j in db.jobs()]
