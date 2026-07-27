"""The visualiser keeps its original parsing, filtering and output semantics."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from dateutil.relativedelta import relativedelta

from tests.conftest import fixture_router, make_client
from trackspace.kb import KnowledgeBase
from worklog_visualizer import visualize_logged_worklogs as viz

LOCAL = viz.LOCAL_TZ


@pytest.mark.parametrize(
    ("spec", "expected", "label"),
    [
        ("5m", timedelta(minutes=5), "5 minutes"),
        ("1h", timedelta(hours=1), "1 hour"),
        ("2d", timedelta(days=2), "2 days"),
        ("4M", relativedelta(months=4), "4 months"),
        ("1Y", relativedelta(years=1), "1 year"),
    ],
)
def test_parse_ago(spec: str, expected: Any, label: str) -> None:
    offset, human = viz.parse_ago(spec)
    assert offset == expected
    assert human == label


@pytest.mark.parametrize("spec", ["", "5", "d", "0d", "-3d", "5x"])
def test_parse_ago_rejects_bad_input(spec: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        viz.parse_ago(spec)


def test_ago_units_are_case_sensitive() -> None:
    assert viz.parse_ago("4m")[0] == timedelta(minutes=4)
    assert viz.parse_ago("4M")[0] == relativedelta(months=4)


@pytest.mark.parametrize(
    "spec", ["2026_04_01-2026_04_30", "20260401-20260430", "2026_04_01 - 2026_04_30"]
)
def test_parse_date_range(spec: str) -> None:
    assert viz.parse_date_range(spec) == (date(2026, 4, 1), date(2026, 4, 30))


@pytest.mark.parametrize(
    "spec", ["2026_04_30-2026_04_01", "2026_02_30-2026_03_01", "nonsense", "20260401"]
)
def test_parse_date_range_rejects_bad_input(spec: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        viz.parse_date_range(spec)


def test_parse_datetime_range() -> None:
    start, end = viz.parse_datetime_range("2026_04_01-09:00-2026_04_01-17:30")
    assert start == datetime(2026, 4, 1, 9, 0)
    assert end == datetime(2026, 4, 1, 17, 30)


@pytest.mark.parametrize(
    "spec",
    ["2026_04_01-17:30-2026_04_01-09:00", "2026_04_01-09:00", "2026_04_01-25:00-2026_04_01-26:00"],
)
def test_parse_datetime_range_rejects_bad_input(spec: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        viz.parse_datetime_range(spec)


def test_resolve_window_prefers_datetime_then_date_then_ago() -> None:
    args = argparse.Namespace(
        datetime_range="2026_04_01-09:00-2026_04_01-17:30", date_range=None, ago=None
    )
    start, end, label = viz.resolve_window(args)
    assert (start.hour, end.hour) == (9, 17)
    assert label == "selected range"

    args = argparse.Namespace(datetime_range=None, date_range="20260401-20260430", ago=None)
    start, end, label = viz.resolve_window(args)
    assert start.time().isoformat() == "00:00:00"
    assert end.time().isoformat() == "23:59:59"

    args = argparse.Namespace(datetime_range=None, date_range=None, ago=None)
    start, end, label = viz.resolve_window(args)
    assert label == "last 30 days"
    assert (end - start).days == 30 or (end - start) == timedelta(days=30)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Suspicious login from 10.0.0.5", "Suspicious login from <IP>"),
        ("Scan from 192.168.1.10:8443", "Scan from <IP>"),
        ("Range 10.0.0.0/24 blocked", "Range <IP> blocked"),
        ("Beacon to 2001:db8::1 observed", "Beacon to <IP> observed"),
        ("Beacon to fe80::1ff:fe23:4567:890a", "Beacon to <IP>"),
        ("", ""),
        ("No addresses here", "No addresses here"),
    ],
)
def test_normalize_title_covers_ipv4_and_ipv6(title: str, expected: str) -> None:
    assert viz.normalize_title(title) == expected


def test_fetch_recent_worklogs_filters_by_author_and_instant(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    start = datetime(2026, 4, 1, 0, 0, tzinfo=LOCAL)
    end = datetime(2026, 4, 30, 23, 59, tzinfo=LOCAL)

    df = viz.fetch_recent_worklogs(client, None, start, end)

    assert list(df.columns) == ["date", "ticket_id", "summary", "hours", "author"]
    assert round(float(df["hours"].sum()), 2) == 9.25
    assert set(df["author"]) == {"Adam Papp"}
    # startedAfter is epoch milliseconds (kb/quirks.md #7).
    worklog_calls = [call for call in session.calls if "/worklog" in call.url]
    assert all(
        call.params["startedAfter"] == int(start.timestamp() * 1000) for call in worklog_calls
    )


def test_fetch_recent_worklogs_for_another_user_resolves_them_first(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    start = datetime(2026, 4, 1, 0, 0, tzinfo=LOCAL)
    end = datetime(2026, 4, 30, 23, 59, tzinfo=LOCAL)

    df = viz.fetch_recent_worklogs(client, "jane", start, end)

    assert set(df["author"]) == {"Jane Doe"}
    assert round(float(df["hours"].sum()), 2) == 3.0
    search_call = next(call for call in session.calls if call.url.endswith("/rest/api/2/search"))
    assert 'worklogAuthor = "jane.doe"' in search_call.params["jql"]


def test_unknown_user_exits_with_the_original_message(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb))
    with pytest.raises(SystemExit, match="No user found matching 'nobody'"):
        viz.fetch_recent_worklogs(
            client,
            "nobody",
            datetime(2026, 4, 1, tzinfo=LOCAL),
            datetime(2026, 4, 30, tzinfo=LOCAL),
        )


def test_sub_day_window_filters_precisely(kb: KnowledgeBase) -> None:
    """worklogDate is day-granular, so the exact instant filter is client-side."""
    client, _ = make_client(kb, fixture_router(kb))
    berlin = ZoneInfo("Europe/Berlin")
    start = datetime(2026, 4, 8, 9, 0, tzinfo=berlin)
    end = datetime(2026, 4, 8, 10, 0, tzinfo=berlin)
    df = viz.fetch_recent_worklogs(client, None, start, end)
    assert len(df) == 1
    assert df.iloc[0]["ticket_id"] == "CLOPSSEC-41501"


def test_empty_result_set_still_builds_a_figure(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb, empty_search=True))
    start = datetime(2026, 4, 1, tzinfo=LOCAL)
    end = datetime(2026, 4, 30, tzinfo=LOCAL)
    df = viz.fetch_recent_worklogs(client, None, start, end)
    assert df.empty
    figure = viz.build_figure(df, start, end, "me", "last 30 days")
    assert figure is not None


def test_build_figure_with_data(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb))
    start = datetime(2026, 4, 1, tzinfo=LOCAL)
    end = datetime(2026, 4, 30, tzinfo=LOCAL)
    df = viz.fetch_recent_worklogs(client, None, start, end)
    figure = viz.build_figure(df, start, end, "Adam Papp", "selected range")
    titles = [ax.get_title(loc="left") for ax in figure.axes]
    assert any("Trackspace worklogs" in title for title in titles)


def test_missing_pat_returns_two(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.delenv("TRACKSPACE_PAT", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    assert viz.main([]) == 2
    assert "TRACKSPACE_PAT environment variable is not set" in capsys.readouterr().err
