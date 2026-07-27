"""End-to-end runs of the scheduler CLI, still without a network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import fixture_router, make_client
from trackspace.kb import KnowledgeBase
from worklog_scheduler import schedule_and_post_worklogs as cli


@pytest.fixture(autouse=True)
def _pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACKSPACE_PAT", "test-token")


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    return tmp_path / "worklog.json"


def patch_client(monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, **router: Any) -> list[Any]:
    """Swap the real client for a fixture-backed one and record the sessions."""
    sessions: list[Any] = []

    def factory(_cfg: Any) -> Any:
        client, session = make_client(kb, fixture_router(kb, **router))
        sessions.append(session)
        return client

    monkeypatch.setattr(cli, "make_client", factory)
    return sessions


def run(*args: str) -> int:
    return cli.main(list(args))


def test_preview_lists_entries_and_posts_nothing(config_path: Path, capsys: Any) -> None:
    code = run(
        "--config", str(config_path), "preview", "--from", "2026-04-01", "--to", "2026-04-07"
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Trackspace worklog scheduler" in out
    assert "Planned worklogs" in out
    assert "7 entries" in out
    assert "Nothing has been posted" in out


def test_header_reports_missing_auth_without_failing(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.delenv("TRACKSPACE_PAT", raising=False)
    assert run("--config", str(config_path), "preview") == 0
    assert "missing (TRACKSPACE_PAT not set)" in capsys.readouterr().out


def test_flags_replace_the_schedule(config_path: Path, capsys: Any) -> None:
    code = run(
        "--config",
        str(config_path),
        "preview",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-03",
        "--recurring",
        "MON-FRI@09:00+15=Standup",
        "--oneoff",
        "2026-04-02@16:00+45=Retro",
        "--exclude",
        "2026-04-03",
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Standup" in out
    assert "Retro" in out
    assert "3 entries" in out  # 1 + 2 Apr standups and the 2 Apr retro; 3 Apr excluded


def test_preview_export_csv(config_path: Path, tmp_path: Path, capsys: Any) -> None:
    export_path = tmp_path / "planned.csv"
    code = run(
        "--config",
        str(config_path),
        "preview",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-07",
        "--export",
        str(export_path),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert export_path.exists()
    # Terminal preview still prints alongside the export.
    assert "Planned worklogs" in out
    assert "Written to" in out
    assert export_path.name in out

    import csv

    with export_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 7
    assert set(rows[0]) == {"date", "day", "time", "duration_min", "comment"}


def test_preview_export_json(config_path: Path, tmp_path: Path, capsys: Any) -> None:
    export_path = tmp_path / "planned.json"
    code = run(
        "--config",
        str(config_path),
        "preview",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-07",
        "--export",
        str(export_path),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Planned worklogs" in out

    import json

    rows = json.loads(export_path.read_text(encoding="utf-8"))
    assert len(rows) == 7
    assert set(rows[0]) == {"date", "day", "time", "duration_min", "comment"}


def test_preview_export_ics(config_path: Path, tmp_path: Path, capsys: Any) -> None:
    export_path = tmp_path / "planned.ics"
    code = run(
        "--config",
        str(config_path),
        "preview",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-07",
        "--export",
        str(export_path),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Planned worklogs" in out

    raw = export_path.read_bytes()
    assert b"\r\n" in raw  # RFC 5545 line endings
    text = raw.decode("utf-8")
    assert text.count("BEGIN:VEVENT") == 7
    assert "UID:trackspace-CLOPSSEC-41456-2026-04-01-1000@trackspace" in text
    assert "DTSTART:" in text
    assert "SUMMARY:Daily" in text


def test_preview_export_unknown_suffix_is_a_config_error(
    config_path: Path, tmp_path: Path, capsys: Any
) -> None:
    export_path = tmp_path / "planned.txt"
    code = run(
        "--config",
        str(config_path),
        "preview",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-07",
        "--export",
        str(export_path),
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_CONFIG
    assert "unknown export format" in out
    assert not export_path.exists()


def test_preview_explain_annotates_rows(config_path: Path, capsys: Any) -> None:
    code = run(
        "--config",
        str(config_path),
        "preview",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-07",
        "--explain",
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Source" in out
    assert "recurring #1 (Daily)" in out
    assert "one-off" not in out  # the default schedule has no one-offs configured


def test_preview_without_explain_has_no_source_annotation(config_path: Path, capsys: Any) -> None:
    code = run(
        "--config", str(config_path), "preview", "--from", "2026-04-01", "--to", "2026-04-07"
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Source" not in out
    assert "recurring #" not in out


def test_import_ics_appends_oneoffs_and_warns_on_all_day(
    config_path: Path, tmp_path: Path, capsys: Any
) -> None:
    """SC-10 wiring: --import-ics appends timed events and skips all-day ones."""
    import_path = tmp_path / "import.ics"
    import_path.write_text(
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART:20260410T090000\r\n"
        "DTEND:20260410T093000\r\n"
        "SUMMARY:Kickoff\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART;VALUE=DATE:20260413\r\n"
        "SUMMARY:Company holiday\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n",
        encoding="utf-8",
    )
    code = run("--config", str(config_path), "config", "--import-ics", str(import_path))
    out = capsys.readouterr().out
    assert code == 0
    assert "Kickoff" in out
    assert "2026-04-10" in out
    assert "skipped all-day .ics event" in out
    assert out.count("Company holiday") == 1  # only in the warning, not a table row


def test_exclude_file_plain_text_merges_with_exclude(
    config_path: Path, tmp_path: Path, capsys: Any
) -> None:
    exclude_file = tmp_path / "holidays.txt"
    exclude_file.write_text("# public holidays\n2026-04-03\n\n2026-04-06\n", encoding="utf-8")
    code = run(
        "--config",
        str(config_path),
        "preview",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-07",
        "--exclude",
        "2026-04-02",
        "--exclude-file",
        str(exclude_file),
    )
    out = capsys.readouterr().out
    assert code == 0
    # 7 default entries minus the 2nd, 3rd (2 entries) and 6th excluded days.
    assert "3 entries" in out


def test_exclude_file_ics_uses_dtstart_dates(
    config_path: Path, tmp_path: Path, capsys: Any
) -> None:
    exclude_file = tmp_path / "holidays.ics"
    exclude_file.write_text(
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "DTSTART;VALUE=DATE:20260403\r\n"
        "SUMMARY:Holiday\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n",
        encoding="utf-8",
    )
    code = run(
        "--config",
        str(config_path),
        "preview",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-07",
        "--exclude-file",
        str(exclude_file),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "5 entries" in out  # 7 default minus the 2 entries on 3 April


def test_exclude_file_bad_date_is_a_config_error(
    config_path: Path, tmp_path: Path, capsys: Any
) -> None:
    exclude_file = tmp_path / "bad.txt"
    exclude_file.write_text("not-a-date\n", encoding="utf-8")
    code = run("--config", str(config_path), "preview", "--exclude-file", str(exclude_file))
    out = capsys.readouterr().out
    assert code == cli.EXIT_CONFIG
    assert "bad date" in out


def test_bad_flag_value_is_a_config_error(config_path: Path, capsys: Any) -> None:
    code = run("--config", str(config_path), "preview", "--recurring", "NOPE@10:00+30=x")
    assert code == cli.EXIT_CONFIG
    assert "unknown weekday" in capsys.readouterr().out


def test_dry_run_submit_sends_nothing(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    sessions = patch_client(monkeypatch, kb)
    code = run("--config", str(config_path), "submit", "--from", "2026-04-01", "--to", "2026-04-07")
    out = capsys.readouterr().out
    assert code == 0
    assert sessions == []  # no client was ever built
    assert "DRY RUN" in out
    assert "Dry run complete" in out
    assert "Re-run with `submit --live`" in out


def test_live_submit_posts_every_entry_then_shows_the_dashboard(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    sessions = patch_client(monkeypatch, kb)
    code = run(
        "--config",
        str(config_path),
        "submit",
        "--live",
        "--yes",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-07",
    )
    out = capsys.readouterr().out
    assert code == 0

    posts = [call for session in sessions for call in session.calls if call.method == "POST"]
    assert len(posts) == 7
    assert posts[0].json_body["started"] == "2026-04-01T10:00:00.000+0200"
    assert posts[0].json_body["timeSpentSeconds"] == 1800
    assert "Posted 7/7 worklogs to CLOPSSEC-41456" in out
    # A fully successful live submit rolls straight into the dashboard.
    assert "Trackspace worklogs —" in out
    assert config_path.exists()


def test_live_submit_reports_failures_and_keeps_going(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    from tests.conftest import FakeResponse

    attempts = {"n": 0}

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        if method == "POST":
            attempts["n"] += 1
            if attempts["n"] == 2:
                return FakeResponse(400, {"errorMessages": ["bad time"]}, url=url, method=method)
            return FakeResponse(201, kb.fixture("add_worklog_created"), url=url, method=method)
        return fixture_router(kb)(method, url, params, body)

    def factory(_cfg: Any) -> Any:
        client, _session = make_client(kb, handler)
        return client

    monkeypatch.setattr(cli, "make_client", factory)

    code = run(
        "--config",
        str(config_path),
        "submit",
        "--live",
        "--yes",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-07",
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_FAILURES
    assert attempts["n"] == 7  # every entry was attempted
    assert "HTTP 400" in out
    assert "Posted 6/7 worklogs" in out


def test_live_submit_without_a_pat_stops_before_any_call(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.delenv("TRACKSPACE_PAT", raising=False)
    code = run("--config", str(config_path), "submit", "--live", "--yes")
    assert code == cli.EXIT_CONFIG
    assert "posting needs a Personal Access Token" in capsys.readouterr().out


def test_submit_with_nothing_to_do(config_path: Path, capsys: Any) -> None:
    code = run(
        "--config",
        str(config_path),
        "submit",
        "--from",
        "2026-04-04",
        "--to",
        "2026-04-05",  # a weekend, no recurring meetings match
    )
    assert code == 0
    assert "Nothing to post" in capsys.readouterr().out


def test_dashboard_renders_and_exports(
    config_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kb: KnowledgeBase,
    tmp_path: Path,
    capsys: Any,
) -> None:
    patch_client(monkeypatch, kb)
    export_path = tmp_path / "worklogs.json"
    code = run(
        "--config",
        str(config_path),
        "dashboard",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-30",
        "--export",
        str(export_path),
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Total logged" in out
    assert "9.2 h" in out or "9.3 h" in out
    assert export_path.exists()
    # The terminal rendering still prints alongside the export.
    assert "Written to" in out
    assert export_path.name in out


def test_dashboard_has_no_target_panel_by_default(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run(
        "--config", str(config_path), "dashboard", "--from", "2026-04-01", "--to", "2026-04-30"
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "target" not in out.lower()


def test_dashboard_target_hours_per_day_shows_the_panel(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run(
        "--config",
        str(config_path),
        "dashboard",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-30",
        "--target-hours-per-day",
        "1",
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Target vs actual" in out
    assert "Target / day" in out
    assert "1.0 h" in out
    # 22 weekdays in April 2026 at 1h/day target = 22.0h expected.
    assert "22.0 h" in out
    assert "22 weekdays" in out
    assert "Gap" in out


def test_dashboard_surfaces_unreadable_issues(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    patch_client(monkeypatch, kb, failing_issues=frozenset({"CLOPSSEC-41677"}))
    code = run(
        "--config", str(config_path), "dashboard", "--from", "2026-04-01", "--to", "2026-04-30"
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "failed CLOPSSEC-41677" in out
    assert "1 issues could not be read fully." in out


def test_config_command_shows_and_exports(config_path: Path, tmp_path: Path, capsys: Any) -> None:
    exported = tmp_path / "exported.json"
    code = run("--config", str(config_path), "config", "--export", str(exported), "--save")
    out = capsys.readouterr().out
    assert code == 0
    assert "Recurring meetings" in out
    assert exported.exists()
    assert config_path.exists()


def test_no_save_leaves_the_config_alone(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase
) -> None:
    patch_client(monkeypatch, kb)
    run(
        "--config",
        str(config_path),
        "--no-save",
        "submit",
        "--from",
        "2026-04-01",
        "--to",
        "2026-04-07",
    )
    assert not config_path.exists()


def test_ctrl_c_exits_with_a_summary(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    def interrupt(*_args: Any, **_kwargs: Any) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "do_preview", interrupt)
    with pytest.raises(SystemExit) as caught:
        run("--config", str(config_path), "preview")
    assert caught.value.code == 130
    assert "Cancelled" in capsys.readouterr().out


def test_biweekly_flag_expands_every_other_week(config_path: Path, capsys: Any) -> None:
    code = run(
        "--config",
        str(config_path),
        "preview",
        "--from",
        "2026-07-01",
        "--to",
        "2026-07-31",
        "--recurring",
        "TUE/2~2026-07-07@14:00+60=Bi-weekly sync",
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "2 entries" in out
    assert "2026-07-07" in out
    assert "2026-07-21" in out
    assert "2026-07-14" not in out


def test_config_shows_the_repeat_column(config_path: Path, capsys: Any) -> None:
    code = run(
        "--config",
        str(config_path),
        "--recurring",
        "TUE/2@14:00+60=Bi-weekly sync",
        "config",
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Repeat" in out
    assert "every other week" in out


def test_dashboard_paginates_the_worklog_fetch(
    config_path: Path, monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    """SC-1: the dashboard now pages every issue's worklogs fully.

    CLOPSSEC-41456 has 7 fixture worklogs and the fake server caps pages at 2
    rows, so a fully-paginating fetch makes several requests carrying startAt —
    where the old paginate=False call made exactly one, unparameterised.
    """
    sessions = patch_client(monkeypatch, kb)
    code = run(
        "--config", str(config_path), "dashboard", "--from", "2026-04-01", "--to", "2026-04-30"
    )
    out = capsys.readouterr().out
    assert code == 0

    worklog_calls = [
        call
        for session in sessions
        for call in session.calls
        if "/worklog" in call.url and "CLOPSSEC-41456" in call.url and call.method == "GET"
    ]
    assert len(worklog_calls) > 1
    assert all(call.params is not None and "startAt" in call.params for call in worklog_calls)

    # Totals are unaffected offline: the fake's unpaginated path used to return
    # the whole fixture in one response, so paging it out changes nothing here.
    assert "9.2 h" in out or "9.3 h" in out


def test_flags_before_the_subcommand_are_not_discarded(config_path: Path, capsys: Any) -> None:
    """argparse would otherwise let the subparser's defaults overwrite them."""
    code = run(
        "--config",
        str(config_path),
        "--issue",
        "OTHER-99",
        "--from",
        "2026-07-01",
        "--to",
        "2026-07-03",
        "preview",
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "OTHER-99" in out
    assert "2026-07-01 → 2026-07-03" in out
