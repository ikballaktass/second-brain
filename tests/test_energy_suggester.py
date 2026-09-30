"""EnergySuggester — high-energy topic suggestion: triggers, topic criteria, message, wiring."""
import asyncio
import os
from datetime import date, datetime, time, timedelta

import pytest
from apscheduler.triggers.interval import IntervalTrigger

from second_brain import main as main_module
from second_brain.models import StateFlags
from second_brain.orchestration.energy_suggester import (
    EnergySuggester,
    Topic,
    is_high_effort,
    near_done_ratio,
    task_counts,
)
from second_brain.proactive.policy import Policy
from second_brain.proactive.scheduler import Scheduler
from second_brain.storage.index_store import IndexStore
from second_brain.storage.state_db import StateDB
from second_brain.storage.vault_repository import VaultRepository

TODAY = date(2026, 10, 1)
HIGH = StateFlags(energy="high", energy_until=TODAY + timedelta(days=2))


def at(hh, mm=0, day=TODAY):
    return datetime.combine(day, time(hh, mm))


@pytest.fixture
def vault(tmp_path):
    return VaultRepository(str(tmp_path / "vault"))


@pytest.fixture
def state(tmp_path):
    with StateDB(str(tmp_path / "state.db")) as db:
        yield db


def write(vault, path, body, days_ago=1, meta=None, now=None):
    vault.write(path, body, meta or {})
    stamp = ((now or at(12)) - timedelta(days=days_ago)).timestamp()
    os.utime(vault.root / path, (stamp, stamp))


def tasks(done, open_):
    return "\n".join(["- [x] bitti"] * done + ["- [ ] açık"] * open_)


class Outbox:
    def __init__(self, error=None) -> None:
        self.sent, self.error = [], error

    async def __call__(self, text):
        if self.error:
            raise self.error
        self.sent.append(text)


def suggester(vault, state, now=at(12), flags=HIGH, index=None, name="İkbal", **policy_kw):
    outbox = Outbox()
    policy = Policy(state, **policy_kw)
    s = EnergySuggester(vault, index, policy, state, lambda: flags, outbox,
                        user_name=name, clock=lambda: now)
    return s, outbox, policy


# --- criteria helpers ---------------------------------------------------------------------

def test_task_counts():
    assert task_counts("- [x] a\n* [X] b\n  + [ ] c\nnot a task [ ]\n- [] d") == (2, 3)


@pytest.mark.parametrize("meta, body, expected", [
    ({"effort": "high"}, "", True),
    ({"difficulty": "Yüksek"}, "", True),
    ({"tags": ["zor", "ai"]}, "", True),
    ({}, "Bu iş #efor istiyor", True),
    ({}, tasks(0, 5), True),
    ({}, tasks(3, 4), False),
    ({"effort": "low", "tags": "kolay"}, "", False),
    ({}, "renk kodu #zorlu değil", False),
])
def test_is_high_effort(meta, body, expected):
    assert is_high_effort(meta, body) is expected


@pytest.mark.parametrize("body, expected", [
    (tasks(3, 1), 0.75),
    (tasks(3, 2), 0.6),
    (tasks(2, 2), None),      # 50 %
    (tasks(2, 0), None),      # too few tasks
    (tasks(4, 0), None),      # already finished
    ("görev yok", None),
])
def test_near_done_ratio(body, expected):
    assert near_done_ratio(body) == expected


# --- triggers -----------------------------------------------------------------------------

def test_sends_one_suggestion_for_a_qualifying_note(vault, state):
    write(vault, "01-Notlar/Tez.md", "Tez yazımı", meta={"effort": "high"})
    s, outbox, policy = suggester(vault, state)

    assert asyncio.run(s()) is True
    assert outbox.sent == ['⚡ İkbal, "Tez" çok efor isteyen bir konu. Enerjin yüksekken ona '
                           'girişmek için iyi bir zaman olabilir. (01-Notlar/Tez.md)']
    assert policy.nudges_today(at(12)) == 1

    s.clock = lambda: at(15)
    assert asyncio.run(s()) is False  # once a day
    assert len(outbox.sent) == 1


@pytest.mark.parametrize("flags", [
    StateFlags(), StateFlags(energy="low"), StateFlags(energy="normal"),
    StateFlags(energy="high", energy_until=TODAY - timedelta(days=1)),
])
def test_only_while_energy_is_high(vault, state, flags):
    write(vault, "01-Notlar/Tez.md", "x", meta={"effort": "high"})
    s, outbox, _ = suggester(vault, state, flags=flags)
    assert asyncio.run(s()) is False and outbox.sent == []


@pytest.mark.parametrize("now, expected", [
    (at(9, 59), False), (at(10), True), (at(21, 59), True), (at(22), False),
])
def test_window_is_ten_to_twenty_two(vault, state, now, expected):
    write(vault, "01-Notlar/Tez.md", "x", meta={"effort": "high"}, now=now)
    s, _, _ = suggester(vault, state, now=now)
    assert asyncio.run(s()) is expected


def test_policy_block_retries_later(vault, state, caplog):
    write(vault, "01-Notlar/Tez.md", "x", meta={"effort": "high"})
    s, outbox, policy = suggester(vault, state, max_nudges_per_day=1)
    policy.record_nudge(at(9), "nudge")

    with caplog.at_level("INFO"):
        assert asyncio.run(s()) is False
    assert "rate_limit" in caplog.text and outbox.sent == []

    s.clock = lambda: at(12, day=TODAY + timedelta(days=1))
    assert asyncio.run(s()) is True


def test_exam_week_wins_over_high_energy(vault, state):
    write(vault, "01-Notlar/Tez.md", "x", meta={"effort": "high"})
    flags = StateFlags(energy="high", exam_week=True, exam_until=TODAY + timedelta(days=3))
    outbox = Outbox()
    policy = Policy(state, state_flags=lambda: flags)
    s = EnergySuggester(vault, None, policy, state, lambda: flags, outbox, clock=lambda: at(12))
    assert asyncio.run(s()) is False and outbox.sent == []


def test_no_qualifying_note_means_no_message(vault, state, caplog):
    write(vault, "01-Notlar/Sadece yeni.md", "yakın zamanda düzenlendi ama ölçüt yok")
    s, outbox, _ = suggester(vault, state)
    with caplog.at_level("INFO"):
        assert asyncio.run(s()) is False
    assert outbox.sent == [] and "no note qualifies" in caplog.text


def test_send_failure_retries(vault, state):
    write(vault, "01-Notlar/Tez.md", "x", meta={"effort": "high"})
    s, _, policy = suggester(vault, state)
    s.send = Outbox(error=ConnectionError("down"))
    assert asyncio.run(s()) is False and policy.nudges_today(at(12)) == 0
    s.send = Outbox()
    assert asyncio.run(s()) is True


# --- topic selection ----------------------------------------------------------------------

def test_candidates_exclude_readme_state_journal_bookmarks_and_old_notes(vault, state):
    effort = {"effort": "high"}
    for path in ("README.md", "State.md", "journal/2026-09-30.md", "bookmarks/b.md"):
        write(vault, path, "x", meta=effort)
    write(vault, "01-Notlar/Eski.md", "x", days_ago=31, meta=effort)
    write(vault, "03-Görevler.md", tasks(4, 1))  # root notes other than README are allowed

    s, _, _ = suggester(vault, state)
    topic = s.pick_topic(at(12))
    assert topic.path == "03-Görevler.md" and topic.reasons == {"near_done"}


def test_more_criteria_win_then_effort_first(vault, state):
    write(vault, "01-Notlar/Bitiyor.md", tasks(4, 1), days_ago=0)
    write(vault, "01-Notlar/Zor.md", "x", meta={"effort": "high"}, days_ago=5)
    write(vault, "01-Notlar/İkisi.md", tasks(6, 2) + "\n#zor", days_ago=9)

    s, _, _ = suggester(vault, state)
    assert s.pick_topic(at(12)).path == "01-Notlar/İkisi.md"

    vault.root.joinpath("01-Notlar/İkisi.md").unlink()
    assert s.pick_topic(at(12)).path == "01-Notlar/Zor.md"  # effort beats near_done


def test_same_note_skipped_for_seven_days(vault, state):
    write(vault, "01-Notlar/Zor.md", "x", meta={"effort": "high"})
    write(vault, "01-Notlar/Bitiyor.md", tasks(4, 1))
    s, outbox, _ = suggester(vault, state)
    asyncio.run(s())
    assert "Zor.md" in outbox.sent[0]

    tomorrow = at(12, day=TODAY + timedelta(days=1))
    s.clock = lambda: tomorrow
    asyncio.run(s())
    assert "Bitiyor.md" in outbox.sent[1]

    week_later = at(12, day=TODAY + timedelta(days=7))
    assert s.pick_topic(week_later).path == "01-Notlar/Zor.md"


def test_frequent_topic_from_index(tmp_path, vault, state):
    from tests.test_index_store import fake_embed

    index = IndexStore(embed=fake_embed, path=str(tmp_path / "index"))
    topic_text = "makine öğrenmesi modeli eğitim verisi doğruluk"
    write(vault, "01-Notlar/ML projesi.md", topic_text, days_ago=2)
    for i in range(3):
        write(vault, f"00-Gelen/ml {i}.md", f"{topic_text} not {i}", days_ago=i + 1)
    write(vault, "00-Gelen/ml eski.md", f"{topic_text} eski", days_ago=20)  # outside 14 days
    for path in vault.list():
        from second_brain.models import Note
        index.upsert(Note(title="", content=vault.read(path).split("---")[-1], path=path))

    s, _, _ = suggester(vault, state, index=index)
    topic = s._assess("01-Notlar/ML projesi.md", at(10), at(12))
    assert "frequent" in topic.reasons

    s_no_index, _, _ = suggester(vault, state, index=None)
    assert "frequent" not in s_no_index._assess("01-Notlar/ML projesi.md", at(10), at(12)).reasons


def test_frequency_needs_three_recent_related_notes(tmp_path, vault, state):
    from tests.test_index_store import fake_embed
    from second_brain.models import Note

    index = IndexStore(embed=fake_embed, path=str(tmp_path / "index"))
    text = "robot kol kinematik hesap"
    write(vault, "01-Notlar/Robot.md", text)
    for i in range(2):
        write(vault, f"00-Gelen/r{i}.md", f"{text} {i}")
    for path in vault.list():
        index.upsert(Note(title="", content=text, path=path))
    s, _, _ = suggester(vault, state, index=index)
    assert "frequent" not in s._assess("01-Notlar/Robot.md", at(10), at(12)).reasons


# --- message ------------------------------------------------------------------------------

def test_messages_per_reason_and_without_name(vault, state):
    s, _, _ = suggester(vault, state)
    near = Topic("01-Notlar/Rapor.md", "Rapor", at(9), {"near_done"}, 0.8)
    freq = Topic("01-Notlar/ML.md", "ML", at(9), {"frequent"})
    assert s.message(near) == ('⚡ İkbal, "Rapor" bitmeye çok yakın (%80). Enerjin yüksekken '
                               'bitirmek için iyi bir zaman olabilir. (01-Notlar/Rapor.md)')
    assert s.message(freq) == ('⚡ İkbal, son günlerde sık sık "ML" üzerinde çalışıyordun. '
                               'Enerjin yüksekken buna bakmak için iyi bir zaman olabilir. '
                               '(01-Notlar/ML.md)')
    anonymous, _, _ = suggester(vault, state, name="  ")
    assert anonymous.message(freq).startswith("⚡ Son günlerde sık sık")


def test_title_drops_note_writer_timestamp(vault, state):
    write(vault, "00-Gelen/2026-09-20-101500 Uzay asansörü.md", "x", meta={"effort": "high"})
    s, outbox, _ = suggester(vault, state)
    asyncio.run(s())
    assert '"Uzay asansörü"' in outbox.sent[0]


# --- scheduler + wiring -------------------------------------------------------------------

def test_runs_as_a_scheduler_job(vault, state):
    now = datetime.combine(date.today(), time(12))
    write(vault, "01-Notlar/Tez.md", "x", meta={"effort": "high"}, now=now)
    flags = StateFlags(energy="high", energy_until=date.today())
    outbox = Outbox()
    s = EnergySuggester(vault, None, Policy(state), state, lambda: flags, outbox,
                        clock=lambda: now)

    async def scenario():
        sched = Scheduler(state)
        sched.add_job("energy_suggestion", s, IntervalTrigger(minutes=30), run_now=True)
        sched._scheduler.start()
        await asyncio.sleep(0.3)
        sched.shutdown()

    asyncio.run(scenario())
    assert len(outbox.sent) == 1
    assert any(j.kind == "energy_suggestion" and j.last_run for j in state.jobs())


def test_build_registers_the_job(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")
    monkeypatch.setenv("USER_NAME", "İkbal")
    monkeypatch.setattr(main_module, "LLMClient", lambda: object())
    monkeypatch.setattr(main_module, "TelegramGateway", lambda **kw: None)
    captured = {}
    real = main_module.Scheduler

    def spy(**kw):
        captured["scheduler"] = real(**kw)
        return captured["scheduler"]

    real_from_config = EnergySuggester.from_config.__func__

    def spy_from_config(cls, *args):
        captured["suggester"] = real_from_config(cls, *args)
        return captured["suggester"]

    monkeypatch.setattr(main_module, "Scheduler", spy)
    monkeypatch.setattr(EnergySuggester, "from_config", classmethod(spy_from_config))
    main_module.build()

    job = captured["scheduler"]._scheduler.get_job("energy_suggestion")
    assert job.trigger.interval == timedelta(minutes=30)
    built = captured["suggester"]
    assert built.user_name == "İkbal"
    assert built.flags.__self__.name == "state_manager"  # StateManager.current
    assert built.policy.state_flags == built.flags
