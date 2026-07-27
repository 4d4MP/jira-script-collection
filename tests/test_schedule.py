"""Schedule expansion — the behaviour inherited verbatim from the original."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trackspace.errors import ConfigurationError
from trackspace.kb import KnowledgeBase
from worklog_scheduler.config import OneOffMeeting, RecurringMeeting, ScheduleConfig
from worklog_scheduler.ics import parse_ics
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


# ---- every-N-weeks recurrence ---------------------------------------------
def test_biweekly_fires_every_other_week(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.start_date, cfg.end_date = "2026-07-01", "2026-07-31"
    cfg.recurring = [
        RecurringMeeting([1], 14, 0, 60, "Bi-weekly sync", interval_weeks=2, anchor="2026-07-07")
    ]
    days = [entry.day.isoformat() for entry in build_entries(cfg)]
    # Tuesdays in July 2026: 7, 14, 21, 28 — every other one from the 7th.
    assert days == ["2026-07-07", "2026-07-21"]


def test_biweekly_anchor_can_sit_outside_the_range(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.start_date, cfg.end_date = "2026-07-01", "2026-07-31"
    cfg.recurring = [
        RecurringMeeting([1], 14, 0, 60, "Bi-weekly sync", interval_weeks=2, anchor="2026-06-30")
    ]
    days = [entry.day.isoformat() for entry in build_entries(cfg)]
    assert days == ["2026-07-14", "2026-07-28"]


def test_biweekly_without_an_anchor_counts_from_the_range_start(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.start_date, cfg.end_date = "2026-07-06", "2026-07-31"
    cfg.recurring = [RecurringMeeting([1], 14, 0, 60, "Sync", interval_weeks=2)]
    days = [entry.day.isoformat() for entry in build_entries(cfg)]
    assert days == ["2026-07-07", "2026-07-21"]


def test_every_third_and_fourth_week(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.start_date, cfg.end_date = "2026-07-01", "2026-08-31"
    cfg.recurring = [
        RecurringMeeting([2], 9, 0, 30, "Every 3", interval_weeks=3, anchor="2026-07-01")
    ]
    days = [entry.day.isoformat() for entry in build_entries(cfg)]
    assert days == ["2026-07-01", "2026-07-22", "2026-08-12"]


def test_weekly_is_unchanged_by_the_interval_field(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    assert len(build_entries(cfg)) == 7
    assert all(meeting.interval_weeks == 1 for meeting in cfg.recurring)


def test_biweekly_still_respects_exclusions(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.start_date, cfg.end_date = "2026-07-01", "2026-07-31"
    cfg.exclude_dates = ["2026-07-21"]
    cfg.recurring = [
        RecurringMeeting([1], 14, 0, 60, "Sync", interval_weeks=2, anchor="2026-07-07")
    ]
    assert [entry.day.isoformat() for entry in build_entries(cfg)] == ["2026-07-07"]


def test_bad_anchor_is_reported(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    cfg.recurring = [RecurringMeeting([1], 14, 0, 60, "Sync", interval_weeks=2, anchor="last-tue")]
    with pytest.raises(ConfigurationError, match="bad anchor date"):
        build_entries(cfg)


def test_parse_recurring_spec_with_interval_and_anchor() -> None:
    assert parse_recurring_spec("TUE/2@14:00+60=Bi-weekly sync") == RecurringMeeting(
        [1], 14, 0, 60, "Bi-weekly sync", interval_weeks=2
    )
    assert parse_recurring_spec("TUE/2~2026-07-21@14:00+60=Sync") == RecurringMeeting(
        [1], 14, 0, 60, "Sync", interval_weeks=2, anchor="2026-07-21"
    )
    # The plain form still parses, and stays weekly.
    assert parse_recurring_spec("MON-FRI@10:00+30=Daily").interval_weeks == 1


@pytest.mark.parametrize(
    "spec",
    ["TUE/0@14:00+60=Sync", "TUE/2~notadate@14:00+60=Sync", "TUE/@14:00+60=Sync"],
)
def test_bad_interval_specs(spec: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_recurring_spec(spec)


def test_repeat_str_reads_naturally() -> None:
    assert RecurringMeeting([1], 9, 0, 30, "x").repeat_str() == "weekly"
    assert RecurringMeeting([1], 9, 0, 30, "x", interval_weeks=2).repeat_str() == "every other week"
    assert (
        RecurringMeeting([1], 9, 0, 30, "x", interval_weeks=2, anchor="2026-07-07").repeat_str()
        == "every other week from 2026-07-07"
    )
    assert RecurringMeeting([1], 9, 0, 30, "x", interval_weeks=3).repeat_str() == "every 3 weeks"


def test_old_configs_without_the_interval_fields_still_load(
    kb: KnowledgeBase, tmp_path: Path
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"recurring": [{"weekdays": [1], "hour": 14, "minute": 0, '
        '"duration_min": 60, "comment": "Sync"}]}',
        encoding="utf-8",
    )
    meeting = ScheduleConfig.load(path, kb).recurring[0]
    assert meeting.interval_weeks == 1
    assert meeting.anchor == ""


def test_interval_fields_survive_a_round_trip(kb: KnowledgeBase, tmp_path: Path) -> None:
    cfg = ScheduleConfig.defaults(kb, today=date(2026, 7, 15))
    cfg.recurring.append(
        RecurringMeeting([1], 15, 0, 60, "Internal sync", interval_weeks=2, anchor="2026-07-21")
    )
    path = tmp_path / "config.json"
    cfg.save(path)
    assert ScheduleConfig.load(path, kb) == cfg


# ---- SC-7: per-meeting blackout dates --------------------------------------
def test_skip_dates_cancel_only_that_meeting(kb: KnowledgeBase) -> None:
    cfg = april(kb)
    # Daily standup skips the 2nd; the other recurring meetings on that day
    # (none here, but the GAC/sync ones on other days) are unaffected.
    cfg.recurring = [
        RecurringMeeting([0, 1, 2, 3, 4], 10, 0, 30, "Daily", skip_dates=["2026-04-02"]),
        RecurringMeeting([3], 13, 0, 30, "Sync"),  # 2026-04-02 is a Thursday
    ]
    entries = build_entries(cfg)
    days_with_daily = {e.day.isoformat() for e in entries if e.comment == "Daily"}
    days_with_sync = {e.day.isoformat() for e in entries if e.comment == "Sync"}
    assert "2026-04-02" not in days_with_daily
    # The other meeting still fires that day — skip_dates is per-meeting only.
    assert "2026-04-02" in days_with_sync


def test_skip_dates_default_to_empty(kb: KnowledgeBase) -> None:
    assert RecurringMeeting([0], 9, 0, 30, "x").skip_dates == []


def test_skip_dates_survive_a_round_trip(kb: KnowledgeBase, tmp_path: Path) -> None:
    cfg = ScheduleConfig.defaults(kb, today=date(2026, 7, 15))
    cfg.recurring.append(
        RecurringMeeting([1], 15, 0, 60, "Internal sync", skip_dates=["2026-07-21", "2026-07-28"])
    )
    path = tmp_path / "config.json"
    cfg.save(path)
    loaded = ScheduleConfig.load(path, kb)
    assert loaded == cfg
    assert loaded.recurring[-1].skip_dates == ["2026-07-21", "2026-07-28"]


def test_old_configs_without_skip_dates_still_load(kb: KnowledgeBase, tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        '{"recurring": [{"weekdays": [1], "hour": 14, "minute": 0, '
        '"duration_min": 60, "comment": "Sync"}]}',
        encoding="utf-8",
    )
    meeting = ScheduleConfig.load(path, kb).recurring[0]
    assert meeting.skip_dates == []


# ---- SC-10: hand-rolled .ics reading ---------------------------------------
_ICS_LITERAL = (
    "BEGIN:VCALENDAR\r\n"
    "VERSION:2.0\r\n"
    # 1) a normal, naive local-time event.
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260410T090000\r\n"
    "DTEND:20260410T093000\r\n"
    "SUMMARY:Kickoff\r\n"
    "END:VEVENT\r\n"
    # 2) a folded SUMMARY line — the leading space on the continuation line is
    #    the RFC 5545 fold marker and is dropped; the trailing space on the
    #    first physical line is real content and survives.
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260411T140000\r\n"
    "DTEND:20260411T150000\r\n"
    "SUMMARY:Folded meeting title that continues \r\n"
    " onto the next physical line\r\n"
    "END:VEVENT\r\n"
    # 3) a UTC (Z-suffixed) start, to be converted into the schedule's zone.
    "BEGIN:VEVENT\r\n"
    "DTSTART:20260412T080000Z\r\n"
    "DTEND:20260412T083000Z\r\n"
    "SUMMARY:UTC event\r\n"
    "END:VEVENT\r\n"
    # 4) an all-day event (VALUE=DATE), which callers must skip.
    "BEGIN:VEVENT\r\n"
    "DTSTART;VALUE=DATE:20260413\r\n"
    "SUMMARY:Company holiday\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_parse_ics_normal_folded_utc_and_all_day() -> None:
    tz = ZoneInfo("Europe/Berlin")
    events = parse_ics(_ICS_LITERAL, tz)
    assert len(events) == 4

    kickoff, folded, utc_event, holiday = events

    assert kickoff.dtstart == datetime(2026, 4, 10, 9, 0, tzinfo=tz)
    assert kickoff.duration_min == 30
    assert kickoff.summary == "Kickoff"
    assert not kickoff.all_day

    assert folded.summary == "Folded meeting title that continues onto the next physical line"

    # 08:00 UTC on 12 April 2026 is 10:00 in Europe/Berlin (CEST, UTC+2).
    assert utc_event.dtstart == datetime(2026, 4, 12, 10, 0, tzinfo=tz)
    assert utc_event.duration_min == 30
    assert not utc_event.all_day

    assert holiday.all_day
    assert holiday.dtstart.date() == date(2026, 4, 13)
    assert holiday.summary == "Company holiday"


def test_parse_ics_duration_fallbacks() -> None:
    tz = ZoneInfo("Europe/Berlin")
    # No DTEND, but a DURATION.
    text = (
        "BEGIN:VEVENT\r\nDTSTART:20260410T090000\r\nDURATION:PT45M\r\nSUMMARY:x\r\nEND:VEVENT\r\n"
    )
    assert parse_ics(text, tz)[0].duration_min == 45

    # Neither DTEND nor DURATION: falls back to 30.
    text = "BEGIN:VEVENT\r\nDTSTART:20260410T090000\r\nSUMMARY:x\r\nEND:VEVENT\r\n"
    assert parse_ics(text, tz)[0].duration_min == 30


# ---- SC-17: alternate duration syntax (+1h30m / +2h / +45m sugar) ---------
@pytest.mark.parametrize(
    ("spec", "expected_minutes"),
    [
        ("MON-FRI@10:00+30=Daily", 30),  # plain digits, unchanged
        ("MON-FRI@10:00+1h30m=Daily", 90),
        ("MON-FRI@10:00+2h=Daily", 120),
        ("MON-FRI@10:00+45m=Daily", 45),
        ("MON-FRI@10:00+1h=Daily", 60),
        ("MON-FRI@10:00+0h30m=Daily", 30),
    ],
)
def test_recurring_spec_accepts_alternate_duration_forms(spec: str, expected_minutes: int) -> None:
    assert parse_recurring_spec(spec).duration_min == expected_minutes


def test_recurring_spec_alternate_duration_with_interval_and_anchor() -> None:
    meeting = parse_recurring_spec("TUE/2~2026-07-21@14:00+1h30m=Sync")
    assert meeting.duration_min == 90
    assert meeting.interval_weeks == 2
    assert meeting.anchor == "2026-07-21"


@pytest.mark.parametrize(
    ("spec", "expected_minutes"),
    [
        ("2026-04-03@13:00+30=Workshop", 30),  # plain digits, unchanged
        ("2026-04-03@13:00+1h30m=Workshop", 90),
        ("2026-04-03@13:00+2h=Workshop", 120),
        ("2026-04-03@13:00+45m=Workshop", 45),
    ],
)
def test_oneoff_spec_accepts_alternate_duration_forms(spec: str, expected_minutes: int) -> None:
    assert parse_oneoff_spec(spec).duration_min == expected_minutes


def test_alternate_duration_is_equivalent_to_its_plain_minutes(kb: KnowledgeBase) -> None:
    sugar = parse_recurring_spec("MON-FRI@10:00+1h30m=Daily")
    plain = parse_recurring_spec("MON-FRI@10:00+90=Daily")
    assert sugar == plain

    sugar_oneoff = parse_oneoff_spec("2026-04-03@13:00+1h30m=Workshop")
    plain_oneoff = parse_oneoff_spec("2026-04-03@13:00+90=Workshop")
    assert sugar_oneoff == plain_oneoff


@pytest.mark.parametrize(
    "spec",
    [
        "MON-FRI@10:00+0h=Daily",  # zero total duration
        "MON-FRI@10:00+h=Daily",  # no number at all
        "MON-FRI@10:00+1h5=Daily",  # trailing digits with no unit
        "MON-FRI@10:00+90x=Daily",  # unknown unit
        "MON-FRI@10:00+=Daily",  # empty duration
        "MON-FRI@10:00+0m=Daily",  # zero total duration, minutes form
        "MON-FRI@10:00+m=Daily",  # unit with no number
        "MON-FRI@10:00+1x30=Daily",  # garbage unit between numbers
    ],
)
def test_bad_recurring_duration_specs_still_reject(spec: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_recurring_spec(spec)


@pytest.mark.parametrize(
    "spec",
    [
        "2026-04-03@13:00+0h=Nope",
        "2026-04-03@13:00+h=Nope",
        "2026-04-03@13:00+1h5=Nope",
        "2026-04-03@13:00+90x=Nope",
        "2026-04-03@13:00+=Nope",
    ],
)
def test_bad_oneoff_duration_specs_still_reject(spec: str) -> None:
    with pytest.raises(ConfigurationError):
        parse_oneoff_spec(spec)


def test_parse_ics_unfolds_lf_only_input_too() -> None:
    """Some generators fold with bare LF rather than CRLF; unfold() copes."""
    tz = ZoneInfo("Europe/Berlin")
    text = "BEGIN:VEVENT\nDTSTART:20260410T090000\nSUMMARY:Split \n title\nEND:VEVENT\n"
    events = parse_ics(text, tz)
    assert events[0].summary == "Split title"
