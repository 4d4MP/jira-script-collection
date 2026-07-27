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
