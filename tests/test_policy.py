"""Policy — quiet hours, calendar busy, daily nudge limit, next_window. Temp StateDB."""
from datetime import datetime, time, timedelta

import pytest

from second_brain.proactive.policy import Decision, Policy, QuietHours
from second_brain.storage.state_db import StateDB

DAY = datetime(2026, 10, 1)


def at(hh: int, mm: int = 0, day: int = 0) -> datetime:
    return DAY + timedelta(days=day, hours=hh, minutes=mm)


@pytest.fixture
def state(tmp_path):
    with StateDB(str(tmp_path / "state.db")) as db:
        yield db


def make(state, **kw) -> Policy:
    return Policy(state, **kw)


# --- quiet hours parsing ------------------------------------------------------------------

def test_parse_quiet_hours():
    assert QuietHours.parse("23:00-08:00") == QuietHours(time(23), time(8))
    assert QuietHours.parse(" 7:30 - 9:05 ") == QuietHours(time(7, 30), time(9, 5))


@pytest.mark.parametrize("bad", ["", "23-08", "23:00", "25:00-08:00", "23:60-08:00",
                                 "08:00-08:00", "gece"])
def test_parse_quiet_hours_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        QuietHours.parse(bad)


@pytest.mark.parametrize("t, quiet", [
    (at(22, 59), False), (at(23, 0), True), (at(2), True), (at(7, 59), True), (at(8), False),
])
def test_wrapping_window_boundaries(t, quiet):
    assert QuietHours.parse("23:00-08:00").contains(t) is quiet


@pytest.mark.parametrize("t, quiet", [(at(12, 59), False), (at(13), True), (at(14), False)])
def test_same_day_window(t, quiet):
    assert QuietHours.parse("13:00-14:00").contains(t) is quiet


def test_quiet_end_before_and_after_midnight():
    q = QuietHours.parse("23:00-08:00")
    assert q.ends_after(at(23, 30)) == at(8, day=1)
    assert q.ends_after(at(3)) == at(8)


# --- decide: quiet hours ------------------------------------------------------------------

def test_allowed_during_day(state):
    assert make(state).decide(at(10), "nudge") == Decision(allowed=True, reason="ok")


@pytest.mark.parametrize("kind", ["reminder", "nudge"])
def test_quiet_hours_block_every_kind(state, kind):
    decision = make(state).decide(at(23, 30), kind)
    assert decision == Decision(False, "quiet_hours", retry_at=at(8, day=1))


def test_invalid_kind(state):
    with pytest.raises(ValueError):
        make(state).decide(at(10), "spam")
    with pytest.raises(ValueError):
        make(state).record_nudge(at(10), "spam")


def test_invalid_limit(state):
    with pytest.raises(ValueError):
        make(state, max_nudges_per_day=-1)


# --- decide: calendar ---------------------------------------------------------------------

def meetings(*blocks):
    """busy_until built from (start, end) blocks."""
    def busy_until(t):
        for start, end in blocks:
            if start <= t < end:
                return end
        return None
    return busy_until


def test_calendar_busy_defers_to_event_end(state):
    policy = make(state, busy_until=meetings((at(10), at(11))))
    assert policy.decide(at(10, 30), "reminder") == Decision(False, "calendar_busy", at(11))
    assert policy.decide(at(11), "reminder").allowed


def test_back_to_back_meetings_chain(state):
    policy = make(state, busy_until=meetings((at(10), at(11)), (at(11), at(12, 30))))
    assert policy.next_window(at(10, 15), "reminder") == at(12, 30)


def test_meeting_ending_in_quiet_hours_waits_until_morning(state):
    policy = make(state, busy_until=meetings((at(21), at(23, 15))))
    assert policy.decide(at(22), "reminder").retry_at == at(8, day=1)


def test_calendar_failure_counts_as_free(state, caplog):
    def broken(t):
        raise ConnectionError("calendar down")

    assert make(state, busy_until=broken).decide(at(10)).allowed
    assert "treating the user as free" in caplog.text


def test_busy_end_in_the_past_is_ignored(state):
    policy = make(state, busy_until=lambda t: t - timedelta(minutes=1))
    assert policy.decide(at(10)).allowed


# --- rate limit ---------------------------------------------------------------------------

def test_daily_limit_applies_to_nudges_only(state):
    policy = make(state, max_nudges_per_day=2)
    policy.record_nudge(at(9), "nudge")
    policy.record_nudge(at(10), "nudge")

    assert policy.nudges_today(at(11)) == 2
    assert policy.decide(at(11), "nudge") == Decision(False, "rate_limit", retry_at=at(8, day=1))
    assert policy.decide(at(11), "reminder").allowed


def test_reminders_do_not_count_but_update_last_nudge(state):
    policy = make(state, max_nudges_per_day=1)
    policy.record_nudge(at(9), "reminder")
    policy.record_nudge(at(9, 30), "reminder")

    assert policy.nudges_today(at(10)) == 0
    assert state.last_nudge() == at(9, 30)
    assert policy.decide(at(10), "nudge").allowed


def test_counter_resets_next_day(state):
    policy = make(state, max_nudges_per_day=1)
    policy.record_nudge(at(20), "nudge")

    assert not policy.decide(at(21), "nudge").allowed
    assert policy.nudges_today(at(9, day=1)) == 0
    assert policy.decide(at(9, day=1), "nudge").allowed

    policy.record_nudge(at(9, day=1), "nudge")
    assert policy.nudges_today(at(10, day=1)) == 1


def test_zero_limit_means_never(state):
    policy = make(state, max_nudges_per_day=0)
    assert policy.decide(at(10), "nudge") == Decision(False, "rate_limit", retry_at=None)
    assert policy.decide(at(10), "reminder").allowed


def test_limit_and_busy_morning_combine(state):
    policy = make(state, max_nudges_per_day=1, busy_until=meetings((at(8, day=1), at(9, day=1))))
    policy.record_nudge(at(12), "nudge")
    assert policy.next_window(at(13), "nudge") == at(9, day=1)


def test_counter_survives_restart(tmp_path):
    path = str(tmp_path / "state.db")
    with StateDB(path) as db:
        make(db, max_nudges_per_day=1).record_nudge(at(9), "nudge")
    with StateDB(path) as db:
        assert not make(db, max_nudges_per_day=1).decide(at(10), "nudge").allowed


# --- wrappers + config --------------------------------------------------------------------

def test_wrappers_match_decide(state):
    policy = make(state)
    assert policy.should_notify({"now": at(10), "kind": "reminder"}) is True
    assert policy.should_notify({"now": at(23, 30)}) is False
    assert policy.next_window(at(10)) == at(10)
    assert policy.next_window(at(23, 30)) == policy.decide(at(23, 30)).retry_at


def test_endless_busy_calendar_gives_up(state, caplog):
    policy = make(state, busy_until=lambda t: t + timedelta(hours=1))
    assert policy.next_window(at(10), "reminder") is None
    assert "No send window found" in caplog.text


def test_from_config(state, monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "22:30-07:00")
    monkeypatch.setenv("POLICY_MAX_NUDGES_PER_DAY", "5")
    policy = Policy.from_config(state)
    assert policy.quiet == QuietHours(time(22, 30), time(7))
    assert policy.max_nudges_per_day == 5


def test_from_config_defaults_and_errors(state, monkeypatch):
    monkeypatch.delenv("QUIET_HOURS", raising=False)
    monkeypatch.delenv("POLICY_MAX_NUDGES_PER_DAY", raising=False)
    policy = Policy.from_config(state)
    assert policy.quiet == QuietHours(time(23), time(8))
    assert policy.max_nudges_per_day == 3

    monkeypatch.setenv("POLICY_MAX_NUDGES_PER_DAY", "çok")
    with pytest.raises(ValueError, match="must be an integer"):
        Policy.from_config(state)
    monkeypatch.setenv("POLICY_MAX_NUDGES_PER_DAY", "3")
    monkeypatch.setenv("QUIET_HOURS", "bozuk")
    with pytest.raises(ValueError, match="QUIET_HOURS"):
        Policy.from_config(state)
