"""StateDB — real SQLite file under tmp_path; no network, never the real state.db."""
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from second_brain.models import Reminder
from second_brain.storage.state_db import SCHEMA_VERSION, Job, StateDB

T0 = datetime(2026, 10, 1, 9, 0, 0)


@pytest.fixture
def db(tmp_path):
    with StateDB(str(tmp_path / "state.db")) as state:
        yield state


# --- schema -------------------------------------------------------------------------------

def test_creates_tables_and_schema_version(tmp_path):
    path = tmp_path / "nested" / "state.db"
    StateDB(str(path)).close()

    conn = sqlite3.connect(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"reminders", "messages", "jobs", "meta"} <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    conn.close()


def test_data_survives_reopen(tmp_path):
    path = str(tmp_path / "state.db")
    with StateDB(path) as first:
        rid = first.add_reminder("hocaya mail", T0)
        first.set_last_nudge(T0)

    with StateDB(path) as second:  # reopening must not wipe or duplicate anything
        assert second.get_reminder(rid).text == "hocaya mail"
        assert second.last_nudge() == T0


def test_in_memory_database_works():
    with StateDB(":memory:") as state:
        state.set_meta("k", "v")
        assert state.get_meta("k") == "v"


# --- reminders ----------------------------------------------------------------------------

def test_add_and_get_reminder(db):
    rid = db.add_reminder("  faturayı öde  ", T0, note_path="notes/x.md")

    assert db.get_reminder(rid) == Reminder(
        id=rid, text="faturayı öde", due=T0, status="pending", note_path="notes/x.md"
    )
    assert db.get_reminder(9999) is None


def test_reminders_due_filters_by_time_and_status(db):
    past = db.add_reminder("geçmiş", T0 - timedelta(hours=1))
    exact = db.add_reminder("tam şimdi", T0)
    db.add_reminder("gelecek", T0 + timedelta(minutes=1))
    sent = db.add_reminder("gönderildi", T0 - timedelta(hours=2))
    db.set_reminder_status(sent, "sent")

    assert [r.id for r in db.reminders_due(now=T0)] == [past, exact]


def test_deferred_reminder_comes_back_at_next_window(db):
    rid = db.add_reminder("annemi ara", T0)
    db.defer_reminder(rid, T0 + timedelta(hours=2))

    assert db.get_reminder(rid).status == "deferred"
    assert db.reminders_due(now=T0 + timedelta(hours=1)) == []
    assert [r.id for r in db.reminders_due(now=T0 + timedelta(hours=2))] == [rid]


def test_list_reminders_sorted_and_filtered(db):
    late = db.add_reminder("b", T0 + timedelta(days=1))
    early = db.add_reminder("a", T0)
    db.set_reminder_status(late, "closed")

    assert [r.id for r in db.list_reminders()] == [early, late]
    assert [r.id for r in db.list_reminders("closed")] == [late]
    with pytest.raises(ValueError):
        db.list_reminders("done")


def test_delete_reminder(db):
    rid = db.add_reminder("sil", T0)
    db.delete_reminder(rid)
    assert db.get_reminder(rid) is None


@pytest.mark.parametrize("call", [
    lambda db: db.set_reminder_status(42, "sent"),
    lambda db: db.defer_reminder(42, T0),
    lambda db: db.delete_reminder(42),
    lambda db: db.mark_job_run("analyze"),
])
def test_missing_rows_raise_key_error(db, call):
    with pytest.raises(KeyError):
        call(db)


def test_invalid_values_are_rejected(db):
    rid = db.add_reminder("x", T0)
    with pytest.raises(ValueError):
        db.set_reminder_status(rid, "done")
    with pytest.raises(ValueError):
        db.add_reminder("   ", T0)
    with pytest.raises(ValueError):
        db.add_reminder("x", "2026-10-01")
    with pytest.raises(ValueError):
        db.save_message(1, "system", "hi")
    with pytest.raises(ValueError):
        db.upsert_job("cleanup", "daily")


def test_timezone_aware_datetimes_are_rejected(db):
    with pytest.raises(ValueError, match="timezone-aware"):
        db.add_reminder("x", T0.replace(tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="timezone-aware"):
        db.reminders_due(now=datetime.now(timezone.utc))


def test_microseconds_do_not_break_comparison(db):
    rid = db.add_reminder("x", T0.replace(microsecond=900_000))
    assert [r.id for r in db.reminders_due(now=T0)] == [rid]


def test_db_check_constraint_guards_status(db):
    with pytest.raises(sqlite3.IntegrityError):
        db._execute(
            "INSERT INTO reminders (text, due, status, created) VALUES ('x', 'd', 'bogus', 'c')"
        )


# --- messages -----------------------------------------------------------------------------

def test_recent_messages_per_chat_oldest_first_with_limit(db):
    for i in range(5):
        db.save_message(111, "user" if i % 2 == 0 else "assistant", f"m{i}")
    db.save_message("222", "user", "başka sohbet")

    recent = db.recent_messages(111, limit=3)

    assert [m.text for m in recent] == ["m2", "m3", "m4"]
    assert [m.role for m in recent] == ["user", "assistant", "user"]
    assert all(m.chat_id == "111" for m in recent)
    assert [m.text for m in db.recent_messages("222")] == ["başka sohbet"]
    with pytest.raises(ValueError):
        db.recent_messages(111, limit=0)


def test_prune_messages(db):
    db.save_message(1, "user", "eski")
    db._execute("UPDATE messages SET ts = ?", ((T0 - timedelta(days=40)).isoformat(),))
    db.save_message(1, "user", "yeni")

    removed = db.prune_messages(older_than=datetime.now() - timedelta(days=30))

    assert removed == 1
    assert [m.text for m in db.recent_messages(1)] == ["yeni"]


# --- jobs ---------------------------------------------------------------------------------

def test_upsert_job_keeps_one_row_per_kind(db):
    db.upsert_job("morning_brief", "08:30")
    db.upsert_job("analyze", "23:00")
    db.upsert_job("morning_brief", "09:00")

    jobs = db.jobs()

    assert [(j.kind, j.schedule) for j in jobs] == [
        ("morning_brief", "09:00"),
        ("analyze", "23:00"),
    ]
    assert all(j.last_run is None for j in jobs)


def test_mark_job_run(db):
    db.upsert_job("journal_check", "21:00")
    db.mark_job_run("journal_check", T0)

    assert db.jobs() == [Job(id=1, kind="journal_check", schedule="21:00", last_run=T0)]


# --- meta ---------------------------------------------------------------------------------

def test_meta_and_last_nudge(db):
    assert db.get_meta("missing") is None
    assert db.get_meta("missing", "d") == "d"
    assert db.last_nudge() is None

    db.set_meta("k", "1")
    db.set_meta("k", "2")
    db.set_last_nudge(T0)

    assert db.get_meta("k") == "2"
    assert db.last_nudge() == T0


# --- threads ------------------------------------------------------------------------------

def test_concurrent_writes_from_threads(db):
    def worker(n):
        for i in range(50):
            db.save_message(n, "user", f"{n}-{i}")
            db.add_reminder(f"r{n}-{i}", T0)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(len(db.recent_messages(n, limit=100)) for n in range(8)) == 400
    assert len(db.list_reminders()) == 400
