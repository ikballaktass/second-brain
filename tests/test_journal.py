"""JournalWriter + JournalNudger (evening nudge through Policy)."""
import asyncio
import threading
from datetime import date, datetime, time, timedelta

import frontmatter
import pytest
from apscheduler.triggers.interval import IntervalTrigger

from second_brain import main as main_module
from second_brain.orchestration.journal_nudger import (
    MESSAGE,
    JournalNudger,
    parse_check_time,
)
from second_brain.proactive.policy import Policy
from second_brain.proactive.scheduler import Scheduler
from second_brain.storage.index_store import IndexStore
from second_brain.storage.state_db import StateDB
from second_brain.storage.vault_repository import VaultRepository
from second_brain.tools.journal_writer import JournalWriter, journal_day, journal_path

DAY = date(2026, 10, 1)


def at(hh, mm=0, day=DAY):
    return datetime.combine(day, time(hh, mm))


@pytest.fixture
def vault(tmp_path):
    return VaultRepository(str(tmp_path / "vault"))


@pytest.fixture
def state(tmp_path):
    with StateDB(str(tmp_path / "state.db")) as db:
        yield db


# --- JournalWriter ------------------------------------------------------------------------

def test_first_entry_creates_the_day_file(vault):
    writer = JournalWriter(vault, clock=lambda: at(14, 5))

    reply = writer.run({"text": "  Bugün sınav vardı, kötü geçti.  "})

    assert reply == "Journal → journal/2026-10-01.md"
    post = frontmatter.load(vault.root / "journal/2026-10-01.md")
    assert dict(post.metadata) == {"type": "journal", "date": "2026-10-01"}
    assert post.content == "**14:05** — Bugün sınav vardı, kötü geçti."


def test_later_entries_append_and_keep_hand_edited_metrics(vault):
    writer = JournalWriter(vault)
    writer.append("sabah koşusu", at(8, 30))
    vault.upsert_frontmatter("journal/2026-10-01.md", {"mood": 4, "people": ["[[Ali]]"]})

    writer.append("akşam arkadaşlarla yemek", at(20, 15))

    post = frontmatter.load(vault.root / "journal/2026-10-01.md")
    assert post["mood"] == 4 and post["people"] == ["[[Ali]]"] and post["type"] == "journal"
    assert post.content == "**08:30** — sabah koşusu\n\n**20:15** — akşam arkadaşlarla yemek"


@pytest.mark.parametrize("now, day", [
    (at(3, 59), DAY - timedelta(days=1)),
    (at(4, 0), DAY),
    (at(23, 59), DAY),
    (at(1, 30, day=DAY + timedelta(days=1)), DAY),
])
def test_day_starts_at_four(now, day):
    assert journal_day(now) == day


def test_after_midnight_goes_to_the_day_that_just_ended(vault):
    JournalWriter(vault).append("gece yarısı düşünceleri", at(1, 30, day=DAY + timedelta(days=1)))
    assert vault.exists("journal/2026-10-01.md")
    assert not vault.exists("journal/2026-10-02.md")


def test_text_is_kept_verbatim(vault):
    text = "Çok yorgunum.\n\nAma **yine de** iyi geçti — #mutlu"
    JournalWriter(vault).append(text, at(22))
    assert frontmatter.load(vault.root / journal_path(DAY)).content.endswith(text)


@pytest.mark.parametrize("args", [{}, {"text": ""}, {"text": "   "}, {"text": 5}])
def test_empty_text_is_rejected(vault, args):
    with pytest.raises(ValueError):
        JournalWriter(vault).run(args)


def test_has_entries(vault):
    writer = JournalWriter(vault)
    assert writer.has_entries(DAY) is False
    vault.write(journal_path(DAY), "", {"type": "journal", "date": "2026-10-01"})
    assert writer.has_entries(DAY) is False  # file exists but nothing written yet
    writer.append("ilk satır", at(12))
    assert writer.has_entries(DAY) is True


def test_parallel_appends_lose_nothing(vault):
    writer = JournalWriter(vault)
    threads = [threading.Thread(target=writer.append, args=(f"giriş {i}", at(12)))
               for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    body = frontmatter.load(vault.root / journal_path(DAY)).content
    assert all(f"giriş {i}" in body for i in range(20))


def test_indexed_as_journal(tmp_path, vault):
    from tests.test_index_store import fake_embed

    index = IndexStore(embed=fake_embed, path=str(tmp_path / "index"))
    writer = JournalWriter(vault, index=index)
    writer.append("swing dersine gittim", at(19))
    writer.append("Ali ile kahve içtik", at(21))

    [hit] = index.search("swing dersi Ali kahve", 1)
    assert (hit.path, hit.type, hit.title) == ("journal/2026-10-01.md", "journal", "Günlük 2026-10-01")
    assert index.count() == 1  # the day is re-embedded, not duplicated


def test_index_failure_keeps_the_entry(vault, caplog):
    class Broken:
        def upsert(self, note):
            raise RuntimeError("down")

    JournalWriter(vault, index=Broken()).append("x", at(12))
    assert vault.exists(journal_path(DAY))
    assert "Index upsert failed" in caplog.text


def test_rebuild_keeps_journal_type(tmp_path, vault):
    from tests.test_index_store import fake_embed

    JournalWriter(vault).append("deniz kenarında yürüyüş", at(18))
    index = IndexStore(embed=fake_embed, path=str(tmp_path / "index"))
    index.rebuild(vault)
    assert index.search("deniz yürüyüş", 1)[0].type == "journal"


# --- JournalNudger ------------------------------------------------------------------------

class Outbox:
    def __init__(self, error=None) -> None:
        self.sent, self.error = [], error

    async def __call__(self, text):
        if self.error:
            raise self.error
        self.sent.append(text)


def nudger(vault, state, now, send=None, policy=None, **policy_kw):
    policy = policy or Policy(state, **policy_kw)
    outbox = send or Outbox()
    n = JournalNudger(JournalWriter(vault), policy, state, outbox, clock=lambda: now)
    return n, outbox, policy


def test_sends_once_in_the_evening_when_journal_is_empty(vault, state):
    n, outbox, policy = nudger(vault, state, at(21, 0))

    assert asyncio.run(n()) is True
    assert outbox.sent == [MESSAGE]
    assert policy.nudges_today(at(21, 1)) == 1  # counts toward the daily limit
    assert state.last_nudge() == at(21, 0)

    n.clock = lambda: at(21, 30)
    assert asyncio.run(n()) is False  # at most once per day
    assert outbox.sent == [MESSAGE]


def test_nothing_before_check_time(vault, state):
    n, outbox, _ = nudger(vault, state, at(20, 59))
    assert asyncio.run(n()) is False and outbox.sent == []


def test_nothing_when_already_journaled(vault, state):
    JournalWriter(vault).append("bugün iyi geçti", at(18))
    n, outbox, _ = nudger(vault, state, at(21, 30))
    assert asyncio.run(n()) is False and outbox.sent == []


def test_policy_block_retries_later_without_marking(vault, state, caplog):
    busy_until = lambda t: at(21, 45) if t < at(21, 45) else None
    n, outbox, policy = nudger(vault, state, at(21, 0), busy_until=busy_until)

    with caplog.at_level("INFO"):
        assert asyncio.run(n()) is False
    assert "calendar_busy" in caplog.text and outbox.sent == []

    n.clock = lambda: at(21, 45)
    assert asyncio.run(n()) is True


def test_daily_limit_blocks_the_nudge(vault, state):
    n, outbox, policy = nudger(vault, state, at(21, 0), max_nudges_per_day=1)
    policy.record_nudge(at(9), "nudge")
    assert asyncio.run(n()) is False and outbox.sent == []


def test_quiet_hours_give_up_for_the_day(vault, state):
    n, outbox, _ = nudger(vault, state, at(23, 30))
    assert asyncio.run(n()) is False and outbox.sent == []
    n.clock = lambda: at(1, 0, day=DAY + timedelta(days=1))  # still DAY's journal, but quiet
    assert asyncio.run(n()) is False and outbox.sent == []


def test_send_failure_is_retried(vault, state, caplog):
    n, _, policy = nudger(vault, state, at(21), send=Outbox(error=ConnectionError("down")))
    assert asyncio.run(n()) is False
    assert policy.nudges_today(at(21)) == 0 and "will retry" in caplog.text

    n.send = Outbox()
    assert asyncio.run(n()) is True


def test_next_day_is_a_new_chance(vault, state):
    n, outbox, _ = nudger(vault, state, at(21))
    asyncio.run(n())
    n.clock = lambda: at(21, day=DAY + timedelta(days=1))
    assert asyncio.run(n()) is True and len(outbox.sent) == 2


@pytest.mark.parametrize("raw, expected", [("21:00", time(21)), (" 7:05 ", time(7, 5))])
def test_parse_check_time(raw, expected):
    assert parse_check_time(raw) == expected


@pytest.mark.parametrize("bad", ["", "21", "25:00", "21:60", "akşam"])
def test_parse_check_time_rejects(bad):
    with pytest.raises(ValueError):
        parse_check_time(bad)


def test_runs_as_a_scheduler_job(vault, state):
    outbox = Outbox()
    evening = datetime.combine(date.today(), time(21, 30))
    n = JournalNudger(JournalWriter(vault), Policy(state), state, outbox, clock=lambda: evening)

    async def scenario():
        sched = Scheduler(state)
        sched.add_job("journal_check", n, IntervalTrigger(minutes=30), run_now=True)
        sched._scheduler.start()
        await asyncio.sleep(0.3)
        sched.shutdown()

    asyncio.run(scenario())
    assert outbox.sent == [MESSAGE]
    assert any(j.kind == "journal_check" and j.last_run for j in state.jobs())


# --- wiring -------------------------------------------------------------------------------

def _build(monkeypatch, tmp_path, journal=True, proactive=True, check="21:00"):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")
    monkeypatch.setenv("JOURNAL_CHECK_TIME", check)
    monkeypatch.setattr(main_module, "LLMClient", lambda: object())
    monkeypatch.setattr(main_module, "TelegramGateway", lambda **kw: None)
    monkeypatch.setitem(main_module.Config.manifest, "journal", journal)
    monkeypatch.setitem(main_module.Config.manifest, "proactive", proactive)
    captured = {}
    real_orch, real_sched = main_module.Orchestrator, main_module.Scheduler

    def spy_sched(**kw):
        captured["scheduler"] = real_sched(**kw)
        return captured["scheduler"]

    monkeypatch.setattr(main_module, "Orchestrator", lambda **kw: captured.update(kw) or real_orch(**kw))
    monkeypatch.setattr(main_module, "Scheduler", spy_sched)
    main_module.build()
    return captured


def test_build_registers_tool_and_evening_job(monkeypatch, tmp_path):
    captured = _build(monkeypatch, tmp_path, check="20:30")
    assert "journal_writer" in {t.name for t in captured["tools"]}
    job = captured["scheduler"]._scheduler.get_job("journal_check")
    assert job.trigger.interval == timedelta(minutes=30)
    with StateDB(str(tmp_path / "state.db")) as db:
        assert "journal_check" in [j.kind for j in db.jobs()]


def test_build_journal_without_proactive_has_tool_but_no_job(monkeypatch, tmp_path):
    captured = _build(monkeypatch, tmp_path, proactive=False)
    assert "journal_writer" in {t.name for t in captured["tools"]}
    assert "scheduler" not in captured


def test_build_journal_off(monkeypatch, tmp_path):
    captured = _build(monkeypatch, tmp_path, journal=False)
    assert "journal_writer" not in {t.name for t in captured["tools"]}
    assert captured["scheduler"]._scheduler.get_job("journal_check") is None


def test_build_rejects_bad_check_time(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="JOURNAL_CHECK_TIME"):
        _build(monkeypatch, tmp_path, check="akşam")
