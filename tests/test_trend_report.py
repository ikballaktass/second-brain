"""TrendReport — descriptive weekly/monthly metric summaries, never a forecast."""
from datetime import date, datetime, time, timedelta

import pytest

from second_brain import main as main_module
from second_brain.storage.vault_repository import VaultRepository
from second_brain.tools.journal_writer import JournalWriter, journal_path
from second_brain.tools.trend_report import NO_FORECAST, TrendReport

TODAY = date(2026, 10, 1)          # a Thursday
NOW = datetime.combine(TODAY, time(20))


@pytest.fixture
def vault(tmp_path):
    return VaultRepository(str(tmp_path / "vault"))


@pytest.fixture
def journal(vault):
    return JournalWriter(vault)


def day(days_ago):
    return TODAY - timedelta(days=days_ago)


def entry(vault, days_ago, text="yazdım", **metrics):
    d = day(days_ago)
    vault.write(journal_path(d), f"**21:00** — {text}",
                {"type": "journal", "date": d.isoformat(), **metrics})


def report(journal, **args):
    return TrendReport(journal, clock=lambda: NOW).run({"period": "week", **args})


# --- basics -------------------------------------------------------------------------------

def test_weekly_summary_with_average_low_high(vault, journal):
    for ago, mood in [(0, 4), (1, 2), (2, 5), (3, 3)]:
        entry(vault, ago, mood=mood)

    lines = report(journal, metrics=["mood"]).splitlines()

    assert lines[0] == "📊 Son 7 gün (25 Eyl – 1 Eki) · 4 günde günlük, 4 günde metrik"
    assert lines[1] == ("ruh hali: ort. 3,5 · en düşük 2 (Çar 30 Eyl) · "
                        "en yüksek 5 (Sal 29 Eyl)")
    assert lines[-1] == NO_FORECAST


def test_week_includes_today_and_the_six_days_before(vault, journal):
    for ago in range(0, 8):
        entry(vault, ago, mood=3)
    assert "7 günde günlük" in report(journal)


def test_month_window_and_threshold(vault, journal):
    for ago in range(0, 6):
        entry(vault, ago, energy=4)
    tr = TrendReport(journal, clock=lambda: NOW)
    assert "enerji: yetersiz veri (6 gün)" in tr.run({"period": "month"})
    entry(vault, 29, energy=2)
    assert "enerji: ort. 3,7" in tr.run({"period": "month"})
    assert tr.run({"period": "month"}).startswith("📊 Son 30 gün (2 Eyl – 1 Eki)")


def test_too_little_data_gives_no_average(vault, journal):
    entry(vault, 0, stress=5)
    entry(vault, 1, stress=4)
    text = report(journal, metrics=["stress"])
    assert "stres: yetersiz veri (2 gün)" in text
    assert "ort." not in text


def test_no_journal_at_all(journal):
    assert report(journal) == ("📊 Son 7 gün (25 Eyl – 1 Eki) için henüz günlük kaydı yok.\n"
                               + NO_FORECAST)


def test_days_without_metrics_are_counted_as_journal_days_only(vault, journal):
    entry(vault, 0)
    entry(vault, 1, mood=3)
    assert "2 günde günlük, 1 günde metrik" in report(journal)


# --- comparison with the previous period ----------------------------------------------------

@pytest.mark.parametrize("before, now, expected", [
    ([2, 2, 2], [4, 4, 3], "önceki 7 güne göre ↑ +1,7"),
    ([5, 5, 4], [3, 3, 3], "önceki 7 güne göre ↓ −1,7"),
    ([3, 3, 4], [3, 4, 3], "önceki 7 güne göre → benzer"),
])
def test_change_against_previous_week(vault, journal, before, now, expected):
    for i, v in enumerate(now):
        entry(vault, i, mood=v)
    for i, v in enumerate(before):
        entry(vault, 7 + i, mood=v)
    assert report(journal, metrics=["mood"]).splitlines()[1].endswith(expected)


def test_no_comparison_when_previous_period_is_thin(vault, journal):
    for i in range(3):
        entry(vault, i, mood=4)
    entry(vault, 8, mood=1)
    assert "önceki" not in report(journal, metrics=["mood"])


# --- data hygiene -------------------------------------------------------------------------

def test_only_integers_one_to_five_count(vault, journal):
    entry(vault, 0, mood=4)
    entry(vault, 1, mood="4")
    entry(vault, 2, mood=7)
    entry(vault, 3, mood=True)
    entry(vault, 4, mood=2)
    entry(vault, 5, mood=3)
    assert "ruh hali: ort. 3,0" in report(journal, metrics=["mood"])


def test_hand_edited_values_count_like_analyzer_ones(vault, journal):
    for ago in range(3):
        entry(vault, ago, mood=3)
    journal.annotate(day(1), {"mood": 5})  # user corrected yesterday
    assert "en yüksek 5" in report(journal, metrics=["mood"])


def test_empty_journal_files_are_ignored(vault, journal):
    vault.write(journal_path(day(0)), "", {"type": "journal", "mood": 5})
    assert "henüz günlük kaydı yok" in report(journal)


# --- output rules -------------------------------------------------------------------------

def test_metric_filter_and_order(vault, journal):
    for ago in range(3):
        entry(vault, ago, mood=3, stress=4, energy=2, productivity=3)
    lines = report(journal, metrics=["stress", "mood"]).splitlines()
    assert [l.split(":")[0] for l in lines[1:3]] == ["ruh hali", "stres"]
    assert "enerji" not in "\n".join(lines)
    assert lines[3] == "Ölçek 1–5; stres için yüksek değer daha fazla stres demek."


def test_every_report_ends_with_the_no_forecast_note(vault, journal):
    for ago in range(10):
        entry(vault, ago, mood=3, energy=3, productivity=3, stress=3)
    for period in ("week", "month"):
        text = TrendReport(journal, clock=lambda: NOW).run({"period": period})
        assert text.endswith(NO_FORECAST)
        for word in ("tahmin edil", "olacak", "gelecek hafta", "yarın"):
            assert word not in text.replace(NO_FORECAST, "")


def test_ties_pick_the_earliest_day(vault, journal):
    for ago in range(3):
        entry(vault, ago, mood=3)
    line = report(journal, metrics=["mood"]).splitlines()[1]
    assert "en düşük 3 (Sal 29 Eyl)" in line and "en yüksek 3 (Sal 29 Eyl)" in line


def test_after_midnight_the_current_journal_day_is_still_yesterday(vault, journal):
    entry(vault, 0, mood=4)
    at_1am = datetime.combine(TODAY + timedelta(days=1), time(1))
    text = TrendReport(journal, clock=lambda: at_1am).run({"period": "week"})
    assert text.startswith("📊 Son 7 gün (25 Eyl – 1 Eki)")


@pytest.mark.parametrize("args, message", [
    ({}, "'period' must be week or month"),
    ({"period": "year"}, "'period' must be week or month"),
    ({"period": "week", "metrics": ["sleep"]}, "'metrics' must be"),
    ({"period": "week", "metrics": "mood"}, "'metrics' must be"),
])
def test_invalid_input(journal, args, message):
    with pytest.raises(ValueError, match=message):
        TrendReport(journal, clock=lambda: NOW).run(args)


def test_schema_offers_only_the_fixed_metrics(journal):
    schema = TrendReport(journal).to_api()["input_schema"]
    assert schema["properties"]["metrics"]["items"]["enum"] == [
        "mood", "energy", "productivity", "stress"]


# --- wiring -------------------------------------------------------------------------------

def _tools(monkeypatch, tmp_path, journal_on):
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "state.db"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")
    monkeypatch.setattr(main_module, "LLMClient", lambda: object())
    monkeypatch.setattr(main_module, "TelegramGateway", lambda **kw: None)
    monkeypatch.setitem(main_module.Config.manifest, "journal", journal_on)
    captured = {}
    real = main_module.Orchestrator
    monkeypatch.setattr(main_module, "Orchestrator", lambda **kw: captured.update(kw) or real(**kw))
    main_module.build()
    return {t.name: t for t in captured["tools"]}


def test_registered_with_the_journal(monkeypatch, tmp_path):
    tools = _tools(monkeypatch, tmp_path, journal_on=True)
    assert tools["trend_report"].journal is tools["journal_writer"]


def test_not_registered_without_the_journal(monkeypatch, tmp_path):
    assert "trend_report" not in _tools(monkeypatch, tmp_path, journal_on=False)
