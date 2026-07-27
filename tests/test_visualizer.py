"""The visualiser: window parsing, fetching, terminal rendering, export."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from dateutil.relativedelta import relativedelta

from tests.conftest import fixture_router, make_client
from trackspace.kb import KnowledgeBase
from trackspace.ui.theme import make_console
from worklog_visualizer import fetch, terminal
from worklog_visualizer import visualize_logged_worklogs as viz
from worklog_visualizer import window as win

LOCAL = win.LOCAL_TZ
BERLIN = ZoneInfo("Europe/Berlin")


# ---- window parsing --------------------------------------------------------
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
    offset, human = win.parse_ago(spec)
    assert offset == expected
    assert human == label


@pytest.mark.parametrize("spec", ["", "5", "d", "0d", "-3d", "5x"])
def test_parse_ago_rejects_bad_input(spec: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        win.parse_ago(spec)


def test_ago_units_are_case_sensitive() -> None:
    assert win.parse_ago("4m")[0] == timedelta(minutes=4)
    assert win.parse_ago("4M")[0] == relativedelta(months=4)


@pytest.mark.parametrize(
    "spec", ["2026_04_01-2026_04_30", "20260401-20260430", "2026_04_01 - 2026_04_30"]
)
def test_parse_date_range(spec: str) -> None:
    assert win.parse_date_range(spec) == (date(2026, 4, 1), date(2026, 4, 30))


@pytest.mark.parametrize(
    "spec", ["2026_04_30-2026_04_01", "2026_02_30-2026_03_01", "nonsense", "20260401"]
)
def test_parse_date_range_rejects_bad_input(spec: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        win.parse_date_range(spec)


def test_parse_datetime_range() -> None:
    start, end = win.parse_datetime_range("2026_04_01-09:00-2026_04_01-17:30")
    assert start == datetime(2026, 4, 1, 9, 0)
    assert end == datetime(2026, 4, 1, 17, 30)


@pytest.mark.parametrize(
    "spec",
    ["2026_04_01-17:30-2026_04_01-09:00", "2026_04_01-09:00", "2026_04_01-25:00-2026_04_01-26:00"],
)
def test_parse_datetime_range_rejects_bad_input(spec: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        win.parse_datetime_range(spec)


def test_resolve_window_prefers_datetime_then_date_then_ago() -> None:
    args = argparse.Namespace(
        datetime_range="2026_04_01-09:00-2026_04_01-17:30", date_range=None, ago=None
    )
    start, end, label = win.resolve_window(args)
    assert (start.hour, end.hour) == (9, 17)
    assert label == "selected range"

    args = argparse.Namespace(datetime_range=None, date_range="20260401-20260430", ago=None)
    start, end, label = win.resolve_window(args)
    assert start.time().isoformat() == "00:00:00"
    assert end.time().isoformat() == "23:59:59"

    args = argparse.Namespace(datetime_range=None, date_range=None, ago=None)
    start, end, label = win.resolve_window(args)
    assert label == "last 30 days"
    assert (end - start).days == 30 or (end - start) == timedelta(days=30)


# ---- title normalisation ---------------------------------------------------
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
    assert fetch.normalize_title(title) == expected


# ---- fetching --------------------------------------------------------------
def test_fetch_filters_by_author_and_instant(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    start = datetime(2026, 4, 1, 0, 0, tzinfo=LOCAL)
    end = datetime(2026, 4, 30, 23, 59, tzinfo=LOCAL)

    df, who = fetch.fetch_recent_worklogs(client, None, start, end)

    assert list(df.columns) == ["date", "ticket_id", "summary", "hours", "author"]
    assert round(float(df["hours"].sum()), 2) == 9.25
    assert set(df["author"]) == {"Adam Papp"}
    assert who == "Adam Papp"
    # startedAfter is epoch milliseconds (kb/quirks.md #7).
    worklog_calls = [call for call in session.calls if "/worklog" in call.url]
    assert all(
        call.params["startedAfter"] == int(start.timestamp() * 1000) for call in worklog_calls
    )


def test_fetch_for_another_user_resolves_them_first(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    df, who = fetch.fetch_recent_worklogs(
        client,
        "jane",
        datetime(2026, 4, 1, tzinfo=LOCAL),
        datetime(2026, 4, 30, 23, 59, tzinfo=LOCAL),
    )
    assert set(df["author"]) == {"Jane Doe"}
    assert who == "Jane Doe"
    assert round(float(df["hours"].sum()), 2) == 3.0
    search_call = next(call for call in session.calls if call.url.endswith("/rest/api/2/search"))
    assert 'worklogAuthor = "jane.doe"' in search_call.params["jql"]


def test_jql_filter_composes_with_worklogdate_bounds(kb: KnowledgeBase) -> None:
    """VZ-2: --jql replaces worklogAuthor but still ANDs the window bounds."""
    client, session = make_client(kb, fixture_router(kb))
    start = datetime(2026, 4, 1, 0, 0, tzinfo=LOCAL)
    end = datetime(2026, 4, 30, 23, 59, tzinfo=LOCAL)

    fetch.fetch_recent_worklogs(
        client, None, start, end, jql_filter="project = CLOPSSEC AND type = Bug"
    )

    search_call = next(call for call in session.calls if call.url.endswith("/rest/api/2/search"))
    assert search_call.params["jql"] == (
        '(project = CLOPSSEC AND type = Bug) AND worklogDate >= "2026-04-01" '
        'AND worklogDate <= "2026-04-30"'
    )


def test_no_jql_filter_uses_the_existing_template(kb: KnowledgeBase) -> None:
    """VZ-2: default (no --jql) behaviour is byte-identical to today's template."""
    client, session = make_client(kb, fixture_router(kb))
    start = datetime(2026, 4, 1, 0, 0, tzinfo=LOCAL)
    end = datetime(2026, 4, 30, 23, 59, tzinfo=LOCAL)

    fetch.fetch_recent_worklogs(client, None, start, end)

    search_call = next(call for call in session.calls if call.url.endswith("/rest/api/2/search"))
    assert search_call.params["jql"] == (
        'worklogAuthor = currentUser() AND worklogDate >= "2026-04-01" '
        'AND worklogDate <= "2026-04-30"'
    )


def test_jql_filter_still_applies_client_side_author_filtering(kb: KnowledgeBase) -> None:
    """VZ-2 / kb/quirks.md #3: arbitrary JQL selects issues, not worklogs — the
    client-side author filter for the current user must still exclude a
    colleague's entries on the same issues."""
    client, _session = make_client(kb, fixture_router(kb))
    start = datetime(2026, 4, 1, 0, 0, tzinfo=LOCAL)
    end = datetime(2026, 4, 30, 23, 59, tzinfo=LOCAL)

    df, who = fetch.fetch_recent_worklogs(client, None, start, end, jql_filter="project = CLOPSSEC")

    assert who == "Adam Papp"
    assert set(df["author"]) == {"Adam Papp"}
    assert "Jane Doe" not in set(df["author"])
    assert round(float(df["hours"].sum()), 2) == 9.25


def test_unknown_user_raises_with_the_original_message(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb))
    with pytest.raises(fetch.UserNotFoundError, match="No user found matching 'nobody'"):
        fetch.fetch_recent_worklogs(
            client,
            "nobody",
            datetime(2026, 4, 1, tzinfo=LOCAL),
            datetime(2026, 4, 30, tzinfo=LOCAL),
        )


def test_sub_day_window_filters_precisely(kb: KnowledgeBase) -> None:
    """worklogDate is day-granular, so the exact instant filter is client-side."""
    client, _ = make_client(kb, fixture_router(kb))
    df, _who = fetch.fetch_recent_worklogs(
        client,
        None,
        datetime(2026, 4, 8, 9, 0, tzinfo=BERLIN),
        datetime(2026, 4, 8, 10, 0, tzinfo=BERLIN),
    )
    assert len(df) == 1
    assert df.iloc[0]["ticket_id"] == "CLOPSSEC-41501"


def test_malformed_worklogs_are_skipped_with_warnings(kb: KnowledgeBase) -> None:
    from tests.conftest import FakeResponse

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        if url.endswith("/myself"):
            return FakeResponse(200, kb.fixture("myself"), url=url, method=method)
        if url.endswith("/rest/api/2/search"):
            return FakeResponse(
                200,
                {
                    "startAt": 0,
                    "maxResults": 100,
                    "total": 1,
                    "issues": [{"key": "CLOPSSEC-99999", "fields": {"summary": "Broken"}}],
                },
                url=url,
                method=method,
            )
        return FakeResponse(200, kb.fixture("issue_worklog_malformed"), url=url, method=method)

    client, _ = make_client(kb, handler)
    warnings: list[str] = []
    df, _who = fetch.fetch_recent_worklogs(
        client,
        None,
        datetime(2026, 4, 1, tzinfo=BERLIN),
        datetime(2026, 4, 30, tzinfo=BERLIN),
        on_warning=warnings.append,
    )
    # An unreadable entry no longer ends the run; it is skipped and reported.
    assert len(df) == 2
    assert any("unreadable worklog timestamp" in message for message in warnings)


def test_empty_result_set(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb, empty_search=True))
    df, _who = fetch.fetch_recent_worklogs(
        client, None, datetime(2026, 4, 1, tzinfo=LOCAL), datetime(2026, 4, 30, tzinfo=LOCAL)
    )
    assert df.empty


# ---- terminal rendering ----------------------------------------------------
def _console(width: int = 120) -> Any:
    console = make_console(record=True)
    console.width = width
    return console


def fetched(kb: KnowledgeBase, start: datetime, end: datetime) -> Any:
    client, _ = make_client(kb, fixture_router(kb))
    df, _who = fetch.fetch_recent_worklogs(client, None, start, end)
    return df


def test_report_shows_the_panels_and_figures(kb: KnowledgeBase) -> None:
    start = datetime(2026, 4, 1, 0, 0, tzinfo=BERLIN)
    end = datetime(2026, 4, 30, 23, 59, tzinfo=BERLIN)
    console = _console(140)
    terminal.render_report(
        console, fetched(kb, start, end), start, end, "Adam Papp", "last 30 days"
    )
    output = console.export_text()

    assert "Trackspace worklogs — last 30 days" in output
    assert "Adam Papp" in output
    assert "Top tickets in period" in output
    assert "Tickets" in output  # the per-ticket table
    assert "Total logged" in output
    assert "9.2 h" in output or "9.3 h" in output
    assert "10 / 22 weekdays" in output
    assert "CLOPSSEC-41456" in output
    assert "█" in output


def test_report_with_no_rows_says_so(kb: KnowledgeBase) -> None:
    start = datetime(2026, 4, 1, tzinfo=BERLIN)
    end = datetime(2026, 4, 30, tzinfo=BERLIN)
    console = _console()
    empty = fetched(kb, datetime(2026, 1, 1, tzinfo=BERLIN), datetime(2026, 1, 2, tzinfo=BERLIN))
    terminal.render_report(console, empty, start, end, "Adam Papp", "last 30 days")
    assert "No worklogs found in the last 30 days." in console.export_text()


@pytest.mark.parametrize(
    ("span_days", "expected_label"),
    [(10, "Wed  1 Apr"), (90, "w/c 30 Mar"), (500, "Apr 2026")],
)
def test_long_windows_bucket_by_week_then_month(span_days: int, expected_label: str) -> None:
    start = date(2026, 4, 1)
    assert terminal._bucket(start, span_days)[1] == expected_label


def test_timeline_collapses_tickets_beyond_the_eighth() -> None:
    entries = [
        terminal.Entry(date(2026, 4, 1), f"KEY-{i}", f"Summary {i}", float(20 - i))
        for i in range(12)
    ]
    console = _console()
    terminal._render_timeline(console, entries, date(2026, 4, 1), date(2026, 4, 2))
    assert "+4 more" in console.export_text()


def test_top_titles_group_by_normalised_title() -> None:
    entries = [
        terminal.Entry(date(2026, 4, 1), "A-1", "Suspicious login from 10.0.0.5", 1.0),
        terminal.Entry(date(2026, 4, 2), "A-2", "Suspicious login from 192.168.1.10", 2.0),
        terminal.Entry(date(2026, 4, 3), "B-1", "Firewall rule review", 0.5),
    ]
    rows = terminal.top_title_rows(entries)
    assert rows[0] == ("Suspicious login from <IP>  (2 tickets)", 3.0)
    assert rows[1] == ("B-1  ·  Firewall rule review", 0.5)


def test_summary_rows_match_the_figure_stats() -> None:
    entries = [
        terminal.Entry(date(2026, 4, 1), "A-1", "One", 2.0),
        terminal.Entry(date(2026, 4, 2), "A-1", "One", 1.0),
    ]
    rows = dict(terminal.summary_rows(entries, date(2026, 4, 1), date(2026, 4, 7)))
    assert rows["Total logged"] == "3.0 h"
    assert rows["Days logged"] == "2 / 5 weekdays"
    assert rows["Avg active day"] == "1.5 h"
    assert rows["Unique tickets"] == "1"
    assert rows["Busiest day"] == "Wed 1 Apr (2.0h)"


# ---- CLI -------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACKSPACE_PAT", "test-token")


def patch_client(monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, **router: Any) -> list[Any]:
    sessions: list[Any] = []

    def factory(_kb: Any) -> Any:
        client, session = make_client(kb, fixture_router(kb, **router))
        sessions.append(session)
        return client

    monkeypatch.setattr(viz, "make_client", factory)
    return sessions


def test_report_run_prints_the_terminal_report(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = viz.main(["report", "--date", "2026_04_01-2026_04_30"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Trackspace worklog visualiser" in out  # boxed header
    assert "present (from TRACKSPACE_PAT)" in out
    assert "Top tickets in period" in out
    assert "worklog entries totalling 9.2h" in out or "worklog entries totalling 9.3h" in out


def test_no_file_is_written_without_an_export_flag(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, tmp_path: Path
) -> None:
    patch_client(monkeypatch, kb)
    monkeypatch.chdir(tmp_path)
    assert viz.main(["report", "--ago", "30d"]) == 0
    assert list(tmp_path.iterdir()) == []


def test_export_writes_an_image_and_still_prints(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, tmp_path: Path, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    destination = tmp_path / "report.png"
    code = viz.main(["report", "--date", "2026_04_01-2026_04_30", "--export", str(destination)])
    out = capsys.readouterr().out
    assert code == 0
    assert destination.exists()
    assert destination.stat().st_size > 0
    assert "Top tickets in period" in out
    assert "Written to" in out


def test_legacy_output_flag_still_exports(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, tmp_path: Path
) -> None:
    patch_client(monkeypatch, kb)
    destination = tmp_path / "legacy.png"
    assert viz.main(["--ago", "30d", "--output", str(destination), "--no-show"]) == 0
    assert destination.exists()


def test_export_can_write_rows(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, tmp_path: Path
) -> None:
    patch_client(monkeypatch, kb)
    destination = tmp_path / "rows.json"
    code = viz.main(["report", "--date", "2026_04_01-2026_04_30", "--export", str(destination)])
    assert code == 0
    rows = json.loads(destination.read_text(encoding="utf-8"))
    assert len(rows) == 11
    assert set(rows[0]) == {"date", "ticket_id", "summary", "hours", "author"}


def test_unknown_export_format_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, tmp_path: Path, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = viz.main(["report", "--ago", "1d", "--export", str(tmp_path / "report.txt")])
    assert code == viz.EXIT_CONFIG
    assert "cannot export to report.txt" in capsys.readouterr().out


def test_empty_jql_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    """VZ-2: an empty --jql value is validated, not silently treated as unset."""
    patch_client(monkeypatch, kb)
    code = viz.main(["report", "--jql", ""])
    assert code == viz.EXIT_CONFIG
    assert "--jql must not be empty" in capsys.readouterr().out


def test_jql_flag_survives_the_subcommand_boundary(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase
) -> None:
    """--jql attaches to both parsers with argparse.SUPPRESS on the subcommand,
    so a pre-subcommand value isn't clobbered by the subparser's own default."""
    patch_client(monkeypatch, kb)
    code = viz.main(["--jql", "project = CLOPSSEC", "report"])
    assert code == viz.EXIT_OK


def test_unknown_user_exits_with_the_original_message(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = viz.main(["report", "--user", "nobody"])
    assert code == viz.EXIT_CONFIG
    assert "No user found matching 'nobody'" in capsys.readouterr().out


def test_auth_failure_keeps_the_original_wording(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    from tests.conftest import FakeResponse

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(401, kb.fixture("errors")["401"]["body"], url=url, method=method)

    def factory(_kb: Any) -> Any:
        client, _session = make_client(kb, handler)
        return client

    monkeypatch.setattr(viz, "make_client", factory)
    code = viz.main(["report", "--ago", "1d"])
    assert code == viz.EXIT_CONFIG
    assert "Auth failed (401). Check TRACKSPACE_PAT." in capsys.readouterr().out


def test_missing_pat_is_reported(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.delenv("TRACKSPACE_PAT", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    assert viz.main(["report", "--ago", "1d"]) == viz.EXIT_CONFIG
    assert "TRACKSPACE_PAT environment variable is not set" in capsys.readouterr().out


def test_ctrl_c_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)

    def interrupt(*_args: Any, **_kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(viz, "run_report", interrupt)
    with pytest.raises(SystemExit) as caught:
        viz.main(["report", "--ago", "1d"])
    assert caught.value.code == 130
    assert "Cancelled" in capsys.readouterr().out


def test_export_canonical_writes_the_shared_shape(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, tmp_path: Path
) -> None:
    patch_client(monkeypatch, kb)
    destination = tmp_path / "canonical.csv"
    code = viz.main(
        ["report", "--date", "2026_04_01-2026_04_30", "--export-canonical", str(destination)]
    )
    assert code == 0
    import csv as _csv

    with destination.open(encoding="utf-8") as handle:
        rows = list(_csv.DictReader(handle))
    assert set(rows[0]) == {"issue", "summary", "date", "hours", "comment", "author"}
    assert rows[0]["comment"] == ""  # the visualiser never collects comments
    assert rows[0]["author"] == "Adam Papp"
