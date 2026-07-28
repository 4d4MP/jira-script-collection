"""End-to-end runs of the issue companion CLI, offline via fixture_router."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from issue_companion import cli
from tests.conftest import Call, FakeResponse, fixture_router, make_client
from trackspace.client import TrackspaceClient
from trackspace.kb import KnowledgeBase


@pytest.fixture(autouse=True)
def _pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACKSPACE_PAT", "test-token")


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich's Console falls back to an 80-column width with no real terminal
    attached (as under pytest's capsys), which would truncate some of the
    fixtures' longer prose fields before this file's assertions ever see them.
    Widening the fake terminal is a test-only concern — the CLI's flex-width
    table columns (trackspace/ui/tables.py) are exercised by test_ui.py."""
    monkeypatch.setenv("COLUMNS", "200")


def patch_client(monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, **router: Any) -> list[Any]:
    """Swap the real client for a fixture-backed one and record the sessions."""
    sessions: list[Any] = []

    def factory(_base_url: str) -> Any:
        client, session = make_client(kb, fixture_router(kb, **router))
        sessions.append(session)
        return client

    monkeypatch.setattr(cli, "make_client", factory)
    return sessions


@dataclass
class MultipartFakeSession:
    """Like ``tests.conftest.FakeSession``, but also models the ``files`` and
    ``headers`` kwargs a real ``requests.Session.request`` accepts for a
    multipart upload. ``tests/conftest.py`` is frozen for this task and its
    shared ``FakeSession`` never needed those before, so this is a small local
    stand-in used only by the upload test below (mirrors the same helper
    appended to ``tests/test_client.py``)."""

    handler: Any
    headers: dict[str, Any] = field(default_factory=dict)
    auth: Any = None
    calls: list[Call] = field(default_factory=list)
    closed: bool = False

    def request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
        files: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> FakeResponse:
        self.calls.append(Call(method, url, params, json, timeout))
        return self.handler(method, url, params, json)

    def close(self) -> None:
        self.closed = True


def patch_multipart_client(
    monkeypatch: pytest.MonkeyPatch, kb: KnowledgeBase, **router: Any
) -> list[MultipartFakeSession]:
    sessions: list[MultipartFakeSession] = []

    def factory(_base_url: str) -> TrackspaceClient:
        fake = MultipartFakeSession(handler=fixture_router(kb, **router))
        sessions.append(fake)
        return TrackspaceClient(
            "test-token",
            kb=kb,
            session=fake,
            sleep=lambda _s: None,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(cli, "make_client", factory)
    return sessions


def run(*args: str) -> int:
    return cli.main(list(args))


ISSUE = "CLOPSSEC-41456"


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------
def test_show_renders_summary_and_status(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run("show", ISSUE)
    out = capsys.readouterr().out
    assert code == 0
    assert "Trackspace issue companion" in out
    assert "Recurring meetings and internal coordination" in out
    assert "In Progress" in out


def test_show_changelog_shows_both_history_entries(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run("show", ISSUE, "--changelog")
    out = capsys.readouterr().out
    assert code == 0
    assert "Changelog" in out
    assert "To Do" in out and "In Progress" in out
    assert "Jane Doe" in out and "Adam Papp" in out


def test_show_attachments_lists_the_fixture_attachment(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run("show", ISSUE, "--attachments")
    out = capsys.readouterr().out
    assert code == 0
    assert "april-report.png" in out


def test_show_links_lists_remote_links(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run("show", ISSUE, "--links")
    out = capsys.readouterr().out
    assert code == 0
    # The "Title" column is fixed-width and truncates; the flex "URL" column does not.
    assert "github.example.invalid/lhsystems/clopssec/pull/42" in out


def test_unknown_issue_key_is_a_clean_error_and_exit_1(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run("show", "NOPE-1")
    out = capsys.readouterr().out
    assert code == 1
    assert "HTTP 404" in out


# ---------------------------------------------------------------------------
# transitions
# ---------------------------------------------------------------------------
def test_transitions_lists_fixture_transitions_and_hints_at_execution(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run("transitions", ISSUE)
    out = capsys.readouterr().out
    assert code == 0
    assert "Reopen" in out
    assert "Open" in out
    assert "--to" in out


def test_transition_executes_by_name(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    sessions = patch_client(monkeypatch, kb)
    code = run("transitions", ISSUE, "--to", "Reopen", "--yes")
    out = capsys.readouterr().out
    assert code == 0
    assert "moved to Open" in out
    posts = [c for c in sessions[0].calls if c.method == "POST"]
    assert len(posts) == 1
    assert posts[0].json_body == {"transition": {"id": "831"}}


def test_transition_executes_by_id_and_matches_case_insensitively(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    sessions = patch_client(monkeypatch, kb)
    assert run("transitions", ISSUE, "--to", "831", "--yes") == 0
    assert run("transitions", ISSUE, "--to", "reOPEN", "--yes") == 0
    capsys.readouterr()
    assert [c.json_body for c in sessions[0].calls if c.method == "POST"] == [
        {"transition": {"id": "831"}}
    ]


def test_transition_carries_a_comment(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    sessions = patch_client(monkeypatch, kb)
    code = run("transitions", ISSUE, "--to", "Reopen", "--comment", "back to you", "--yes")
    capsys.readouterr()
    assert code == 0
    posts = [c for c in sessions[0].calls if c.method == "POST"]
    assert posts[0].json_body["update"] == {"comment": [{"add": {"body": "back to you"}}]}


def test_unavailable_transition_is_refused_without_posting(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """The live list is the validation set — the graph depends on current status."""
    sessions = patch_client(monkeypatch, kb)
    code = run("transitions", ISSUE, "--to", "Start Progress", "--yes")
    out = capsys.readouterr().out
    assert code == 1
    assert "No transition 'Start Progress' available" in out
    assert "831=Reopen" in out
    assert [c for c in sessions[0].calls if c.method == "POST"] == []


# ---------------------------------------------------------------------------
# comment
# ---------------------------------------------------------------------------
def test_comment_list_pages_by_returned_count(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """The fixture has 3 comments; the fake server caps pages at 2 rows."""
    sessions = patch_client(monkeypatch, kb)
    code = run("comment", ISSUE, "list")
    out = capsys.readouterr().out
    assert code == 0
    assert "Kicking off the daily coordination log" in out
    session = sessions[0]
    starts = [c.params["startAt"] for c in session.calls if c.params and "startAt" in c.params]
    assert starts == [0, 2]


def test_comment_add_update_delete_hit_the_right_method_and_url(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    sessions = patch_client(monkeypatch, kb)
    assert run("comment", ISSUE, "add", "--body", "hello", "--yes") == 0
    add_call = sessions[0].calls[0]
    assert add_call.method == "POST"
    assert add_call.url.endswith(f"/issue/{ISSUE}/comment")
    assert add_call.json_body == {"body": "hello"}

    assert run("comment", ISSUE, "update", "--id", "10100", "--body", "edited", "--yes") == 0
    update_call = sessions[1].calls[0]
    assert update_call.method == "PUT"
    assert update_call.url.endswith(f"/issue/{ISSUE}/comment/10100")
    assert update_call.json_body == {"body": "edited"}

    assert run("comment", ISSUE, "delete", "--id", "10100", "--yes") == 0
    delete_call = sessions[2].calls[0]
    assert delete_call.method == "DELETE"
    assert delete_call.url.endswith(f"/issue/{ISSUE}/comment/10100")
    capsys.readouterr()


def test_comment_delete_without_yes_needs_a_terminal(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run("comment", ISSUE, "delete", "--id", "10100")
    out = capsys.readouterr().out
    assert code == 2
    assert "terminal" in out


# ---------------------------------------------------------------------------
# attach
# ---------------------------------------------------------------------------
def test_attach_list_shows_the_fixture_attachment(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run("attach", ISSUE, "list")
    out = capsys.readouterr().out
    assert code == 0
    assert "april-report.png" in out


def test_attach_upload_refuses_a_file_over_the_upload_limit(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    # attachment_meta.json fixes uploadLimit at 10485760 bytes.
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (10_485_760 + 1))
    code = run("attach", ISSUE, "upload", str(big), "--yes")
    out = capsys.readouterr().out
    assert code == 2
    assert "upload limit" in out


def test_attach_upload_succeeds_for_a_small_file(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    sessions = patch_multipart_client(monkeypatch, kb)
    small = tmp_path / "small.png"
    small.write_bytes(b"tiny file")
    code = run("attach", ISSUE, "upload", str(small), "--yes")
    out = capsys.readouterr().out
    assert code == 0
    assert "may-report.png" in out
    upload_calls = [c for c in sessions[0].calls if c.method == "POST" and "attachments" in c.url]
    assert len(upload_calls) == 1


def test_attach_delete_works(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    sessions = patch_client(monkeypatch, kb)
    code = run("attach", ISSUE, "delete", "--id", "10500", "--yes")
    capsys.readouterr()
    assert code == 0
    call = sessions[0].calls[0]
    assert call.method == "DELETE"
    assert call.url.endswith("/attachment/10500")


# ---------------------------------------------------------------------------
# link
# ---------------------------------------------------------------------------
def test_link_add_validates_the_type_name(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run("link", ISSUE, "add", "--type", "NotARealType", "--to", "CLOPSSEC-41501", "--yes")
    out = capsys.readouterr().out
    assert code == 2
    assert "unknown link type" in out
    for name in ("Blocks", "Cloners", "Duplicate", "Relates"):
        assert name in out


def test_link_add_with_a_valid_type_creates_the_link(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    sessions = patch_client(monkeypatch, kb)
    code = run("link", ISSUE, "add", "--type", "Blocks", "--to", "CLOPSSEC-41501", "--yes")
    capsys.readouterr()
    assert code == 0
    post_calls = [c for c in sessions[0].calls if c.method == "POST"]
    assert len(post_calls) == 1
    assert post_calls[0].json_body == {
        "type": {"name": "Blocks"},
        "inwardIssue": {"key": "CLOPSSEC-41501"},
        "outwardIssue": {"key": ISSUE},
    }


def test_link_add_remote_sends_global_id_when_given(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    sessions = patch_client(monkeypatch, kb)
    code = run(
        "link",
        ISSUE,
        "add-remote",
        "--url",
        "https://example.invalid/pr/1",
        "--title",
        "PR #1",
        "--global-id",
        "system=pr-1",
        "--yes",
    )
    capsys.readouterr()
    assert code == 0
    call = sessions[0].calls[0]
    assert call.json_body == {
        "object": {"url": "https://example.invalid/pr/1", "title": "PR #1"},
        "globalId": "system=pr-1",
    }


def test_link_delete_and_delete_remote(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    sessions = patch_client(monkeypatch, kb)
    assert run("link", ISSUE, "delete", "--id", "30001", "--yes") == 0
    assert sessions[0].calls[0].method == "DELETE"
    assert sessions[0].calls[0].url.endswith("/issueLink/30001")

    assert run("link", ISSUE, "delete-remote", "--id", "20001", "--yes") == 0
    assert sessions[1].calls[0].method == "DELETE"
    assert sessions[1].calls[0].url.endswith(f"/issue/{ISSUE}/remotelink/20001")
    capsys.readouterr()


def test_link_list_shows_issue_and_remote_links(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    code = run("link", ISSUE, "list")
    out = capsys.readouterr().out
    assert code == 0
    assert "SecOps runbook" in out


# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------
def test_missing_pat_is_a_configuration_error_exit_2(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    # Deliberately not patching make_client here: the real make_client() calls
    # require_pat() and must fail before any (fixture-backed or real) network
    # call is attempted.
    monkeypatch.delenv("TRACKSPACE_PAT", raising=False)
    code = run("show", ISSUE)
    out = capsys.readouterr().out
    assert code == 2
    assert "TRACKSPACE_PAT" in out


def test_success_exit_code_is_zero(
    kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    patch_client(monkeypatch, kb)
    assert run("show", ISSUE) == 0
    capsys.readouterr()
