"""Schedule expansion — the behaviour inherited verbatim from the original."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from trackspace.errors import ConfigurationError
from trackspace.kb import KnowledgeBase
from worklog_scheduler.config import OneOffMeeting, RecurringMeeting, ScheduleConfig
from worklog_scheduler.schedule import (
    build_entries,
    parse_oneoff_spec,
    parse_recurring_spec,
    parse_weekdays,
    quick_range,
    total_minutes,
)


def april(kb: KnowledgeBase) -> ScheduleConfig:
    cfg = ScheduleConfig.defaults(kb, today=date(2026, 4, 15))
    cfg.start_date, cfg.end_date = "2026-04-01", "2026-04-07"
    return cfg


def test_default_schedule_comes_from_the_knowledge_base(kb: KnowledgeBase) -> None:
    cfg = ScheduleConfig.defaults(kb, today=date(2026, 4, 15))
    assert cfg.issue_key == "CLOPSSEC-41456"
    assert cfg.timezone == "Europe/Berlin"
    assert cfg.dry_run is True
    assert cfg.start_date == "2026-04-01"
    assert cfg.end_date == "2026-04-28"
    assert [m.comment for m in cfg.recurring] == ["Daily", "SecOps - technical sync", "GAC GDN"]


def test_recurring_expansion_and_ordering(kb: KnowledgeBase) -> None:
    entries = build_entries(april(kb))
    # 1-7 April 2026 is Wed..Tue: 5 weekdays of "Daily", one Tuesday sync, one Friday GAC.
    assert len(entries) == 7
    assert [e.started.strftime("%Y-%m-%d %H:%M") for e in entries] == [
        "2026-04-01 10:00",
        "2026-04-02 10:00",
        "2026-04-03 10:00",
        "2026-04-03 13:00",
        "2026-04-06 10:00",
        "2026-04-07 10:00",
        "2026-04-07 14:00",
    ]
    assert total_minutes(entries) == 5 * 30 + 30 + 60


def test_start_times_carry_the_configured_zone(kb: KnowledgeBase) -> None:
    entry = build_entries(april(kb))[0]
    assert entry.started.utcoffset() is not None
    assert entry.started.strftime("%Y-%m-%dT%H:%M:%S.000%z") == "2026-04-01T10:00:00.000+0200"


def test_excluded_dates_suppress_recurring_and_oneoffs(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.exclude_dates = ["2026-04-02", "2026-04-03"]
    cfg.oneoffs = [OneOffMeeting("2026-04-02", 9, 0, 45, "Workshop")]
    days = {entry.day.isoformat() for entry in build_entries(cfg)}
    assert "2026-04-02" not in days
    assert "2026-04-03" not in days


def test_oneoff_outside_the_range_is_skipped_but_kept(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.oneoffs = [OneOffMeeting("2026-05-20", 9, 0, 45, "Later")]
    assert all(entry.comment != "Later" for entry in build_entries(cfg))
    assert cfg.oneoffs  # still configured, just not in range


def test_weekend_oneoffs_are_logged(kb: KnowledgeBase) -> None:
    """Weekends are only empty because the defaults are Mon-Fri (kb/quirks.md #9)."""
    cfg = april(kb)
    cfg.oneoffs = [OneOffMeeting("2026-04-04", 9, 0, 60, "Saturday incident")]
    assert any(entry.comment == "Saturday incident" for entry in build_entries(cfg))


def test_saturday_recurring_is_logged_too(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.recurring = [RecurringMeeting([5], 9, 0, 60, "Weekend duty")]
    assert len(build_entries(cfg)) == 1


def test_reversed_range_yields_nothing(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.start_date, cfg.end_date = "2026-04-07", "2026-04-01"
    assert build_entries(cfg) == []


def test_bad_configuration_is_reported(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.timezone = "Mars/Olympus"
    with pytest.raises(ConfigurationError, match="unknown timezone"):
        build_entries(cfg)

    cfg = april(kb)
    cfg.start_date = "not-a-date"
    with pytest.raises(ConfigurationError, match="bad date"):
        build_entries(cfg)

    cfg = april(kb)
    cfg.exclude_dates = ["2026-13-40"]
    with pytest.raises(ConfigurationError, match="bad excluded date"):
        build_entries(cfg)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("this-month", (date(2026, 4, 1), date(2026, 4, 30))),
        ("last-month", (date(2026, 3, 1), date(2026, 3, 31))),
        ("last-30", (date(2026, 3, 17), date(2026, 4, 15))),
        ("this-week", (date(2026, 4, 13), date(2026, 4, 19))),
    ],
)
def test_quick_ranges(name: str, expected: tuple[date, date]) -> None:
    assert quick_range(name, today=date(2026, 4, 15)) == expected


def test_unknown_quick_range() -> None:
    with pytest.raises(ConfigurationError, match="unknown range"):
        quick_range("yesterday")


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("MON-FRI", [0, 1, 2, 3, 4]),
        ("weekdays", [0, 1, 2, 3, 4]),
        ("daily", [0, 1, 2, 3, 4, 5, 6]),
        ("Mon,Wed,Fri", [0, 2, 4]),
        ("1", [1]),
        ("SAT-SUN", [5, 6]),
        ("SUN-MON", [0, 6]),
    ],
)
def test_parse_weekdays(spec: str, expected: list[int]) -> None:
    assert parse_weekdays(spec) == expected


def test_parse_weekdays_rejects_nonsense() -> None:
    with pytest.raises(ConfigurationError):
        parse_weekdays("Funday")
    with pytest.raises(ConfigurationError):
        parse_weekdays("9")


def test_parse_recurring_spec() -> None:
    meeting = parse_recurring_spec("MON-FRI@10:00+30=Daily")
    assert meeting == RecurringMeeting([0, 1, 2, 3, 4], 10, 0, 30, "Daily")


def test_parse_oneoff_spec() -> None:
    meeting = parse_oneoff_spec("2026-04-03@13:00+30=GAC GDN")
    assert meeting == OneOffMeeting("2026-04-03", 13, 0, 30, "GAC GDN")


@pytest.mark.parametrize(
    "spec",
    [
        "MON-FRI@10:00+30",  # no comment
        "MON-FRI@25:00+30=Daily",  # impossible hour
        "MON-FRI@10:00+0=Daily",  # zero duration
        "@10:00+30=Daily",  # no days
    ],
)
def test_bad_recurring_specs(spec: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_recurring_spec(spec)


@pytest.mark.parametrize(
    "spec",
    ["2026-04-31@13:00+30=Nope", "20260403@13:00+30=Nope", "2026-04-03@13:60+30=Nope"],
)
def test_bad_oneoff_specs(spec: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_oneoff_spec(spec)


def test_config_round_trip_keeps_the_historical_json_shape(
    kb: KnowledgeBase, tmp_path: Path
) -> None:
    cfg = ScheduleConfig.defaults(kb, today=date(2026, 4, 15))
    cfg.oneoffs = [OneOffMeeting("2026-04-03", 13, 0, 30, "GAC")]
    path = tmp_path / "config.json"
    cfg.save(path)

    raw = path.read_text(encoding="utf-8")
    assert '"jira_base"' in raw  # the original key name, so old configs still load

    loaded = ScheduleConfig.load(path, kb)
    assert loaded == cfg


def test_unknown_keys_are_dropped_and_broken_files_fall_back(
    kb: KnowledgeBase, tmp_path: Path
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"issue_key": "OTHER-1", "future_key": 42}', encoding="utf-8")
    loaded = ScheduleConfig.load(path, kb)
    assert loaded.issue_key == "OTHER-1"
    assert not hasattr(loaded, "future_key")

    path.write_text("{not json", encoding="utf-8")
    assert ScheduleConfig.load(path, kb).issue_key == "CLOPSSEC-41456"

    missing = tmp_path / "nope.json"
    assert ScheduleConfig.load(missing, kb).issue_key == "CLOPSSEC-41456"
