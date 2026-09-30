"""State flags: StateFlags model, NoteWriter.update_state, StateManager tool, Policy rules."""
import asyncio
from datetime import date, datetime, time, timedelta

import frontmatter
import pytest

from second_brain import main as main_module
from second_brain.models import StateFlags
from second_brain.orchestration.journal_nudger import JournalNudger
from second_brain.proactive.policy import Decision, Policy
from second_brain.storage.index_store import IndexStore
from second_brain.storage.state_db import StateDB
from second_brain.storage.vault_repository import VaultRepository
from second_brain.tools.journal_writer import JournalWriter
from second_brain.tools.note_writer import STATE_PATH, NoteWriter
from second_brain.tools.state_manager import StateManager

TODAY = date(2026, 10, 1)


def at(hh, mm=0, day=TODAY):
    return datetime.combine(day, time(hh, mm))


@pytest.fixture
def vault(tmp_path):
    return VaultRepository(str(tmp_path / "vault"))


@pytest.fixture
def state(tmp_path):
    with StateDB(str(tmp_path / "state.db")) as db:
        yield db


@pytest.fixture
def manager(vault):
    return StateManager(NoteWriter(vault), today=lambda: TODAY)


def fm(vault):
    return frontmatter.load(vault.root / STATE_PATH)


# --- StateFlags ---------------------------------------------------------------------------

def test_flags_expire_on_their_end_date():
    flags = StateFlags(energy="low", energy_until=TODAY, exam_week=True, exam_until=TODAY)
    assert flags.energy_on(TODAY) == "low" and flags.exam_on(TODAY)
    tomorrow = TODAY + timedelta(days=1)
    assert flags.energy_on(tomorrow) == "normal" and not flags.exam_on(tomorrow)


def test_flags_without_end_date_stay_until_changed():
    flags = StateFlags(energy="high", exam_week=True)
    assert flags.energy_on(TODAY + timedelta(days=365)) == "high"
    assert flags.exam_on(TODAY + timedelta(days=365))


def test_from_frontmatter_is_tolerant_of_hand_edits():
    flags = StateFlags.from_frontmatter({
        "energy": "LOW ", "energy_until": "2026-10-03", "exam_week": True,
        "exam_until": date(2026, 10, 8), "cycle_phase": "Luteal", "updated": "2026-10-01",
    })
    assert flags == StateFlags("low", date(2026, 10, 3), True, date(2026, 10, 8), "luteal",
                               date(2026, 10, 1))
    junk = StateFlags.from_frontmatter({"energy": "süper", "exam_week": "yes",
                                        "cycle_phase": "x", "energy_until": "yarın"})
    assert junk == StateFlags()


# --- NoteWriter.update_state --------------------------------------------------------------

def test_update_state_creates_merges_and_removes(vault):
    writer = NoteWriter(vault)
    writer.update_state({"energy": "low", "energy_until": "2026-10-04"})
    vault.upsert_frontmatter(STATE_PATH, {"note_to_self": "kendi alanım"})  # hand edit

    writer.update_state({"energy_until": None, "exam_week": True})

    post = fm(vault)
    assert post["type"] == "state" and post["energy"] == "low" and post["exam_week"] is True
    assert "energy_until" not in post.metadata
    assert post["note_to_self"] == "kendi alanım"


def test_state_note_is_never_indexed(tmp_path, vault):
    from tests.test_index_store import fake_embed

    index = IndexStore(embed=fake_embed, path=str(tmp_path / "index"))
    NoteWriter(vault, index=index).update_state({"cycle_phase": "luteal"})
    assert index.count() == 0


# --- StateManager tool --------------------------------------------------------------------

def test_low_energy_defaults_to_three_days(vault, manager):
    reply = manager.run({"action": "set", "energy": "low"})
    post = fm(vault)
    assert (post["energy"], post["energy_until"], post["updated"]) == (
        "low", "2026-10-04", "2026-10-01")
    assert reply == "State → State.md · energy: low (until 2026-10-04)"


def test_exam_week_defaults_to_seven_days_or_uses_until(vault, manager):
    manager.run({"action": "set", "exam_week": True})
    assert fm(vault)["exam_until"] == "2026-10-08"
    manager.run({"action": "set", "exam_week": True, "until": "2026-10-12"})
    assert fm(vault)["exam_until"] == "2026-10-12"


def test_normal_and_false_clear_the_flags(vault, manager):
    manager.run({"action": "set", "energy": "low", "exam_week": True})
    manager.run({"action": "set", "energy": "normal", "exam_week": False})
    post = fm(vault)
    assert post["energy"] == "normal" and post["exam_week"] is False
    assert "energy_until" not in post.metadata and "exam_until" not in post.metadata
    assert manager.run({"action": "show"}) == "No active state flags."


def test_cycle_phase_is_recorded_and_can_be_cleared(vault, manager):
    manager.run({"action": "set", "cycle_phase": "menstrual"})
    assert fm(vault)["cycle_phase"] == "menstrual"
    assert "cycle: menstrual" in manager.run({"action": "show"})
    manager.run({"action": "set", "cycle_phase": "none"})
    assert "cycle_phase" not in fm(vault).metadata


def test_only_mentioned_fields_change(vault, manager):
    manager.run({"action": "set", "exam_week": True})
    manager.run({"action": "set", "energy": "high"})
    post = fm(vault)
    assert post["exam_week"] is True and post["energy"] == "high"


def test_show_summary(manager):
    manager.run({"action": "set", "energy": "high", "exam_week": True, "cycle_phase": "luteal"})
    assert manager.run({"action": "show"}) == (
        "energy: high (until 2026-10-04) · exam week until 2026-10-08 · cycle: luteal")


@pytest.mark.parametrize("args, message", [
    ({"action": "set"}, "nothing to set"),
    ({"action": "set", "energy": "tired"}, "'energy' must be one of"),
    ({"action": "set", "exam_week": "yes"}, "'exam_week' must be true or false"),
    ({"action": "set", "cycle_phase": "spring"}, "'cycle_phase' must be one of"),
    ({"action": "set", "energy": "low", "until": "next week"}, "'until' must be YYYY-MM-DD"),
    ({"action": "set", "energy": "low", "until": "2026-09-30"}, "in the past"),
    ({"action": "clear"}, "unknown action"),
])
def test_invalid_input(manager, args, message):
    with pytest.raises(ValueError, match=message):
        manager.run(args)


def test_logs_never_contain_values(manager, caplog):
    with caplog.at_level("INFO"):
        manager.run({"action": "set", "cycle_phase": "menstrual", "energy": "low"})
    assert "menstrual" not in caplog.text and "low" not in caplog.text
    assert "cycle_phase" in caplog.text


def test_current_without_file_is_neutral(manager):
    assert manager.current() == StateFlags()


# --- Policy -------------------------------------------------------------------------------

def policy_with(state, flags, **kw):
    return Policy(state, state_flags=lambda: flags, **kw)


def test_exam_week_silences_nudges_but_not_reminders(state):
    policy = policy_with(state, StateFlags(exam_week=True, exam_until=TODAY + timedelta(days=2)))

    nudge = policy.decide(at(12), "nudge")
    assert (nudge.allowed, nudge.reason) == (False, "state_flags")
    assert nudge.retry_at == at(8, day=TODAY + timedelta(days=3))  # first morning after exams
    assert policy.decide(at(12), "reminder") == Decision(allowed=True, reason="ok")


def test_low_energy_allows_one_nudge_a_day(state):
    policy = policy_with(state, StateFlags(energy="low", energy_until=TODAY))
    assert policy.decide(at(10), "nudge").allowed
    policy.record_nudge(at(10), "nudge")

    blocked = policy.decide(at(14), "nudge")
    assert (blocked.allowed, blocked.reason) == (False, "state_flags")
    assert blocked.retry_at == at(8, day=TODAY + timedelta(days=1))
    assert policy.decide(at(14), "reminder").allowed


def test_regular_limit_still_reports_rate_limit(state):
    policy = policy_with(state, StateFlags(energy="low"), max_nudges_per_day=1)
    policy.record_nudge(at(9), "nudge")
    assert policy.decide(at(10), "nudge").reason == "rate_limit"


def test_high_energy_changes_nothing(state):
    policy = policy_with(state, StateFlags(energy="high"), max_nudges_per_day=2)
    policy.record_nudge(at(9), "nudge")
    assert policy.decide(at(10), "nudge").allowed
    policy.record_nudge(at(10), "nudge")
    assert policy.decide(at(11), "nudge").reason == "rate_limit"


def test_cycle_phase_changes_nothing(state):
    policy = policy_with(state, StateFlags(cycle_phase="menstrual"), max_nudges_per_day=2)
    policy.record_nudge(at(9), "nudge")
    assert policy.decide(at(10), "nudge").allowed


def test_expired_flags_are_ignored(state):
    policy = policy_with(state, StateFlags(exam_week=True, exam_until=TODAY - timedelta(days=1)))
    assert policy.decide(at(12), "nudge").allowed


def test_month_long_exam_period_still_finds_a_window(state):
    policy = policy_with(state, StateFlags(exam_week=True, exam_until=TODAY + timedelta(days=30)))
    assert policy.next_window(at(12), "nudge") == at(8, day=TODAY + timedelta(days=31))


def test_unreadable_flags_fall_back_to_defaults(state, caplog):
    def broken():
        raise OSError("State.md locked")

    assert Policy(state, state_flags=broken).decide(at(12), "nudge").allowed
    assert "Could not read state flags" in caplog.text


def test_policy_reads_the_real_state_note(vault, state, manager):
    manager.run({"action": "set", "exam_week": True})
    policy = Policy(state, state_flags=manager.current)
    assert policy.decide(at(12), "nudge").reason == "state_flags"


def test_exam_week_holds_back_the_journal_nudge(vault, state, manager):
    manager.run({"action": "set", "exam_week": True})
    sent = []

    async def send(text):
        sent.append(text)

    policy = Policy(state, state_flags=manager.current)
    nudger = JournalNudger(JournalWriter(vault), policy, state, send, clock=lambda: at(21))
    assert asyncio.run(nudger()) is False and sent == []


# --- wiring -------------------------------------------------------------------------------

def test_build_registers_tool_and_feeds_policy(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")
    monkeypatch.setattr(main_module, "LLMClient", lambda: object())
    monkeypatch.setattr(main_module, "TelegramGateway", lambda **kw: None)
    captured = {}
    real_orch, real_from_config = main_module.Orchestrator, main_module.Policy.from_config

    def spy_policy(state, busy_until=None, state_flags=None):
        captured["policy"] = real_from_config(state, busy_until=busy_until, state_flags=state_flags)
        return captured["policy"]

    monkeypatch.setattr(main_module, "Orchestrator", lambda **kw: captured.update(kw) or real_orch(**kw))
    monkeypatch.setattr(main_module.Policy, "from_config", staticmethod(spy_policy))
    main_module.build()

    manager = next(t for t in captured["tools"] if t.name == "state_manager")
    assert captured["policy"].state_flags == manager.current
    assert manager.note_writer is next(t for t in captured["tools"] if t.name == "note_writer")
