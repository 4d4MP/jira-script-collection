"""Offline tests for NT-1, the read-only instance probe.

Everything runs through ``tests/conftest.py``'s ``fixture_router``/``make_client``
— no network, no PAT. Per the build's hard rules this file never touches
``tests/conftest.py``, ``kb/fixtures/*`` or any other test file.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from instance_probe import cli as probe_cli
from instance_probe import probe
from tests.conftest import FakeResponse, fixture_router, make_client
from trackspace.client import TrackspaceClient
from trackspace.errors import ConfigurationError
from trackspace.kb import KnowledgeBase


@pytest.fixture(autouse=True)
def _pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACKSPACE_PAT", "test-token")


# ---------------------------------------------------------------------------
# Step-level: probe.run_probe against the fixture-backed client
# ---------------------------------------------------------------------------
def test_fields_step_finds_the_custom_fields(kb: KnowledgeBase, client: TrackspaceClient) -> None:
    report = probe.run_probe(client, kb, steps=["fields"])
    finding = report.findings[0]
    assert finding.ok is True
    assert finding.endpoint == "field_list"
    assert finding.evidence["custom_count"] == 2
    ids = {f["id"] for f in finding.evidence["custom_fields"]}
    # Real ids from the 2026-07-28 probe run; the fixture no longer invents any.
    assert ids == {"customfield_21500", "customfield_18303"}
    for entry in finding.evidence["custom_fields"]:
        assert "name" in entry
        assert "clauseNames" in entry


def test_project_step_reports_the_real_name(kb: KnowledgeBase, client: TrackspaceClient) -> None:
    report = probe.run_probe(client, kb, steps=["project"])
    finding = report.findings[0]
    assert finding.ok is True
    assert finding.evidence["key"] == "CLOPSSEC"
    assert finding.evidence["name"] == "CloudOps Security"
    assert len(finding.evidence["issue_types"]) == 7


def test_createmeta_step_scopes_to_the_project(kb: KnowledgeBase, client: TrackspaceClient) -> None:
    report = probe.run_probe(client, kb, steps=["createmeta"])
    finding = report.findings[0]
    assert finding.ok is True
    assert finding.evidence["projects"][0]["key"] == "CLOPSSEC"


def test_statuses_step_lists_the_global_catalogue(
    kb: KnowledgeBase, client: TrackspaceClient
) -> None:
    report = probe.run_probe(client, kb, steps=["statuses"])
    finding = report.findings[0]
    assert finding.ok is True
    names = {status["name"] for status in finding.evidence["statuses"]}
    assert names == {"Open", "Reopened", "In Progress", "Analysis", "Resolved", "Closed"}


def test_transitions_step_lists_transition_ids(kb: KnowledgeBase, client: TrackspaceClient) -> None:
    report = probe.run_probe(client, kb, steps=["transitions"])
    finding = report.findings[0]
    assert finding.ok is True
    ids = {t["id"] for t in finding.evidence["transitions"]}
    assert ids == {"831"}


def test_permissions_step_includes_the_denied_administer(
    kb: KnowledgeBase, client: TrackspaceClient
) -> None:
    report = probe.run_probe(client, kb, steps=["permissions"])
    finding = report.findings[0]
    assert finding.ok is True
    permissions = finding.evidence["permissions"]
    assert permissions["ADMINISTER"]["havePermission"] is False
    assert "ADMINISTER" in finding.summary


def test_error_shape_step_treats_404_as_success_and_captures_the_body(
    kb: KnowledgeBase, client: TrackspaceClient
) -> None:
    report = probe.run_probe(client, kb, steps=["error-shape"])
    finding = report.findings[0]
    assert finding.ok is True
    bogus = finding.evidence["bogus_key"]
    assert bogus["status"] == 404
    assert bogus["body"]
    assert "errorMessages" in bogus["body"]
    malformed = finding.evidence["malformed_key"]
    assert malformed["status"] in (400, 404)


# ---------------------------------------------------------------------------
# A step whose endpoint 500s is recorded failed; the rest still run.
# ---------------------------------------------------------------------------
def test_a_failing_endpoint_does_not_abort_the_other_steps(kb: KnowledgeBase) -> None:
    base_handler = fixture_router(kb)

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        if url.endswith("/field"):
            return FakeResponse(500, kb.fixture("errors")["500"]["body"], url=url, method=method)
        return base_handler(method, url, params, body)

    client, _session = make_client(kb, handler)
    report = probe.run_probe(client, kb, steps=probe.DEFAULT_STEPS)
    by_name = {finding.name: finding for finding in report.findings}
    assert by_name["fields"].ok is False
    assert "GET field_list failed" in by_name["fields"].summary
    # Every other step still ran and succeeded.
    assert by_name["project"].ok is True
    assert by_name["createmeta"].ok is True
    assert by_name["statuses"].ok is True
    assert by_name["transitions"].ok is True
    assert by_name["permissions"].ok is True
    assert by_name["error-shape"].ok is True


# ---------------------------------------------------------------------------
# Rate-limit step: opt-in only, hard-capped, honestly worded.
# ---------------------------------------------------------------------------
def test_rate_limit_step_is_absent_from_the_default_run(
    kb: KnowledgeBase, client: TrackspaceClient
) -> None:
    report = probe.run_probe(client, kb, steps=probe.DEFAULT_STEPS)
    assert "rate-limit" not in {finding.name for finding in report.findings}


def test_rate_limit_step_reports_the_required_honest_wording(
    kb: KnowledgeBase, client: TrackspaceClient
) -> None:
    report = probe.run_probe(client, kb, steps=["rate-limit"], rate_limit_burst=5)
    finding = report.findings[0]
    assert finding.ok is True
    assert finding.summary.startswith("0/5 requests returned 429 at burst=5 on ")
    assert "no rate limit" not in finding.summary.lower()
    assert "confirmed" not in finding.summary.lower()
    assert len(finding.evidence["calls"]) == 5
    assert all(call["status"] == 200 for call in finding.evidence["calls"])
    assert all("elapsed_ms" in call for call in finding.evidence["calls"])


def test_rate_limit_burst_at_the_hard_cap_is_allowed(
    kb: KnowledgeBase, client: TrackspaceClient
) -> None:
    report = probe.run_probe(client, kb, steps=["rate-limit"], rate_limit_burst=20)
    finding = report.findings[0]
    assert finding.summary.startswith("0/20 requests returned 429 at burst=20 on ")


def test_rate_limit_burst_over_the_hard_cap_is_a_configuration_error(
    kb: KnowledgeBase, client: TrackspaceClient
) -> None:
    with pytest.raises(ConfigurationError):
        probe.run_probe(client, kb, steps=["rate-limit"], rate_limit_burst=21)


# ---------------------------------------------------------------------------
# GET-only guard
# ---------------------------------------------------------------------------
def test_get_only_guard_refuses_a_doctored_post_endpoint(kb: KnowledgeBase) -> None:
    doctored_data = copy.deepcopy(kb.raw)
    doctored_data["endpoints"]["field_list"]["method"] = "POST"
    doctored_kb = KnowledgeBase(doctored_data, kb.fixtures_dir.parent)
    with pytest.raises(ConfigurationError):
        probe.assert_get_only(doctored_kb)


def test_get_only_guard_passes_the_real_kb(kb: KnowledgeBase) -> None:
    probe.assert_get_only(kb)  # every real endpoint the probe uses is a GET


# ---------------------------------------------------------------------------
# CLI: end-to-end runs
# ---------------------------------------------------------------------------
def _patch_probe_client(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, handler: Any = None
) -> list[Any]:
    sessions: list[Any] = []

    def factory(_kb: KnowledgeBase, _token: str) -> Any:
        real_client, session = make_client(kb, handler or fixture_router(kb))
        sessions.append(session)
        return real_client

    monkeypatch.setattr(probe_cli, "make_client", factory)
    return sessions


def run(*args: str) -> int:
    return probe_cli.main(list(args))


def test_default_cli_run_prints_the_header_and_findings_and_makes_only_gets(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    sessions = _patch_probe_client(monkeypatch, kb)
    code = run()
    out = capsys.readouterr().out
    assert code == probe_cli.EXIT_OK
    assert "Trackspace instance probe" in out
    assert "Probe findings" in out
    assert "this tool never writes the KB" in out
    all_calls = [call for session in sessions for call in session.calls]
    assert all_calls  # requests were actually made
    assert all(call.method == "GET" for call in all_calls)


def test_only_flag_restricts_the_steps_that_run(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    _patch_probe_client(monkeypatch, kb)
    code = run("--only", "fields", "--only", "project")
    out = capsys.readouterr().out
    assert code == probe_cli.EXIT_OK
    assert "fields" in out
    assert "project" in out
    assert "statuses" not in out
    assert "createmeta" not in out


def test_export_writes_json_only_when_the_flag_is_passed(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, tmp_path: Path, capsys: Any
) -> None:
    _patch_probe_client(monkeypatch, kb)
    export_path = tmp_path / "findings.json"
    code = run("--export", str(export_path))
    out = capsys.readouterr().out
    assert code == probe_cli.EXIT_OK
    assert export_path.exists()
    data = json.loads(export_path.read_text())
    assert data["note"] == (
        "findings are for human fold-in to kb/trackspace.json; this tool never writes the KB"
    )
    assert "probed_at" in data
    assert len(data["findings"]) == len(probe.DEFAULT_STEPS)
    assert "Written to" in out
    assert export_path.name in out


def test_no_export_flag_writes_no_file(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, tmp_path: Path, capsys: Any
) -> None:
    _patch_probe_client(monkeypatch, kb)
    monkeypatch.chdir(tmp_path)
    code = run()
    out = capsys.readouterr().out
    assert code == probe_cli.EXIT_OK
    assert list(tmp_path.iterdir()) == []
    # The terminal rendering still prints even without an export.
    assert "Probe findings" in out


def test_rate_limit_flag_included_only_when_passed(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    _patch_probe_client(monkeypatch, kb)
    code = run("--rate-limit-burst")
    out = capsys.readouterr().out
    assert code == probe_cli.EXIT_OK
    assert "rate-limit" in out
    assert "requests returned 429 at burst=5 on" in out


def test_rate_limit_flag_over_the_cap_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    _patch_probe_client(monkeypatch, kb)
    code = run("--rate-limit-burst", "21")
    assert code == probe_cli.EXIT_CONFIG


def test_a_failed_step_gives_exit_1_while_the_others_still_show(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    base_handler = fixture_router(kb)

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        if url.endswith("/status"):
            return FakeResponse(500, kb.fixture("errors")["500"]["body"], url=url, method=method)
        return base_handler(method, url, params, body)

    _patch_probe_client(monkeypatch, kb, handler=handler)
    code = run()
    out = capsys.readouterr().out
    assert code == probe_cli.EXIT_FAILURES
    assert "statuses" in out
    assert "FAILED" in out
    assert "fields" in out  # the rest of the run still shows in the report


def test_missing_pat_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    monkeypatch.delenv("TRACKSPACE_PAT", raising=False)
    code = run()
    out = capsys.readouterr().out
    assert code == probe_cli.EXIT_CONFIG
    assert "TRACKSPACE_PAT" in out


def test_ctrl_c_exits_130_with_a_summary(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, capsys: Any
) -> None:
    def interrupt(*_args: Any, **_kwargs: Any) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(probe, "run_probe", interrupt)
    _patch_probe_client(monkeypatch, kb)
    with pytest.raises(SystemExit) as caught:
        run()
    assert caught.value.code == 130
    assert "Cancelled" in capsys.readouterr().out
