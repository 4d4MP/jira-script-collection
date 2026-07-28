"""Transport behaviour: pagination, error mapping, retries, and what must not retry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import requests

from tests.conftest import Call, FakeResponse, fixture_router, make_client
from trackspace.client import TrackspaceClient
from trackspace.errors import (
    ApiError,
    AuthError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    ServerError,
    TrackspaceError,
    TransportError,
)
from trackspace.kb import KnowledgeBase


def test_bearer_auth_header_and_accept(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    assert session.headers["Authorization"] == "Bearer test-token"
    assert session.headers["Accept"] == "application/json"
    client.close()
    assert session.closed


def test_myself(client: Any) -> None:
    assert client.myself()["name"] == "adam.papp"


def test_search_pagination_advances_by_returned_count(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    jql = kb.jql(
        "worklogs_by_current_user_in_range", start_date="2026-04-01", end_date="2026-04-30"
    )
    issues = client.search_issues(jql, ["summary"])
    assert [issue["key"] for issue in issues] == [
        "CLOPSSEC-41456",
        "CLOPSSEC-41501",
        "CLOPSSEC-41502",
        "CLOPSSEC-41677",
        "CLOPSSEC-41703",
    ]
    # The server returned 3 of a requested 100, so the second call must start at 3.
    starts = [call.params["startAt"] for call in session.calls if call.params]
    assert starts == [0, 3]


def test_search_progress_callback(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb))
    seen: list[tuple[int, int]] = []
    client.search_issues(
        "jql", ["summary"], on_progress=lambda done, total: seen.append((done, total))
    )
    assert seen == [(3, 5), (5, 5)]


def test_empty_search(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb, empty_search=True))
    assert client.search_issues("jql", ["summary"]) == []


def test_search_without_total_stops_after_one_page(kb: KnowledgeBase) -> None:
    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(200, {"issues": [{"key": "X-1", "fields": {}}]}, url=url, method=method)

    client, session = make_client(kb, handler)
    assert len(client.search_issues("jql", ["summary"])) == 1
    assert len(session.calls) == 1


def test_search_stops_on_an_empty_page_even_if_total_lies(kb: KnowledgeBase) -> None:
    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(200, {"issues": [], "total": 999}, url=url, method=method)

    client, session = make_client(kb, handler)
    assert client.search_issues("jql", ["summary"]) == []
    assert len(session.calls) == 1


def test_issue_worklogs_paginates_and_sends_started_after(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    worklogs = client.issue_worklogs("CLOPSSEC-41501", started_after_ms=1_700_000_000_000)
    assert [entry["id"] for entry in worklogs] == ["900201", "900202", "900203"]
    assert all(call.params["startedAfter"] == 1_700_000_000_000 for call in session.calls)
    assert [call.params["startAt"] for call in session.calls] == [0, 2]


def test_issue_worklogs_unpaginated_sends_no_parameters(kb: KnowledgeBase) -> None:
    """The scheduler's historical single-request behaviour (kb/quirks.md #8)."""
    client, session = make_client(kb, fixture_router(kb))
    worklogs = client.issue_worklogs("CLOPSSEC-41456", paginate=False)
    assert len(worklogs) == 7
    assert session.calls[0].params is None


def test_issue_key_is_url_quoted(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    client.issue_worklogs("CLOPS SEC-1", paginate=False)
    assert "CLOPS%20SEC-1" in session.calls[0].url


def test_add_worklog_formats_started_and_body(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    started = datetime(2026, 4, 1, 10, 0, tzinfo=ZoneInfo("Europe/Berlin"))
    created = client.add_worklog(
        "CLOPSSEC-41456", started=started, duration_seconds=1800, comment="Daily"
    )
    assert created["id"] == "900401"
    body = session.calls[0].json_body
    assert body == {
        "timeSpentSeconds": 1800,
        "started": "2026-04-01T10:00:00.000+0200",
        "comment": "Daily",
    }


def test_find_user_first_match_and_miss(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb))
    assert client.find_user("jane")["name"] == "jane.doe"
    assert client.find_user("nobody") is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, AuthError),
        (403, ForbiddenError),
        (404, NotFoundError),
        (400, ApiError),
    ],
)
def test_error_mapping(kb: KnowledgeBase, status: int, expected: type[ApiError]) -> None:
    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(status, {"errorMessages": ["nope"]}, url=url, method=method)

    client, session = make_client(kb, handler)
    with pytest.raises(expected):
        client.myself()
    # Nothing in the 4xx family is retried.
    assert len(session.calls) == 1


def test_error_message_matches_the_original_format(kb: KnowledgeBase) -> None:
    long_body = "x" * 500

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(400, url=url, method=method, text=long_body)

    client, _ = make_client(kb, handler)
    with pytest.raises(ApiError) as caught:
        client.myself()
    assert str(caught.value) == f"HTTP 400: {'x' * 200}"


def test_rate_limit_is_retried_and_honours_retry_after(kb: KnowledgeBase) -> None:
    slept: list[float] = []
    responses = iter(
        [
            FakeResponse(
                429, {"errorMessages": ["slow down"]}, headers={"Retry-After": "3"}, method="GET"
            ),
            FakeResponse(200, {"name": "adam.papp"}, method="GET"),
        ]
    )

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return next(responses)

    client, session = make_client(kb, handler)
    client._sleep = slept.append
    assert client.myself()["name"] == "adam.papp"
    assert len(session.calls) == 2
    assert slept == [3.0]


def test_rate_limit_exhausted_raises(kb: KnowledgeBase) -> None:
    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(429, {"errorMessages": ["slow down"]}, url=url, method=method)

    client, session = make_client(kb, handler, max_retries=2)
    with pytest.raises(RateLimitError) as caught:
        client.myself()
    assert caught.value.retry_after is None
    assert len(session.calls) == 3  # first attempt plus two retries


def test_server_errors_are_retried(kb: KnowledgeBase) -> None:
    responses = iter(
        [
            FakeResponse(503, {"errorMessages": ["down"]}, method="GET"),
            FakeResponse(200, {"name": "adam.papp"}, method="GET"),
        ]
    )
    client, session = make_client(kb, lambda *args: next(responses))
    assert client.myself()["name"] == "adam.papp"
    assert len(session.calls) == 2


def test_server_error_gives_up_eventually(kb: KnowledgeBase) -> None:
    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(500, {"errorMessages": ["down"]}, url=url, method=method)

    client, _ = make_client(kb, handler, max_retries=1)
    with pytest.raises(ServerError):
        client.myself()


def test_timeouts_become_transport_errors_and_retry(kb: KnowledgeBase) -> None:
    calls = {"n": 0}

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        calls["n"] += 1
        raise requests.Timeout("too slow")

    client, _ = make_client(kb, handler, max_retries=2)
    with pytest.raises(TransportError, match="timed out"):
        client.myself()
    assert calls["n"] == 3


def test_connection_errors_become_transport_errors(kb: KnowledgeBase) -> None:
    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        raise requests.ConnectionError("reset by peer")

    client, _ = make_client(kb, handler, max_retries=0)
    with pytest.raises(TransportError, match="failed"):
        client.myself()


def test_worklog_post_is_never_retried(kb: KnowledgeBase) -> None:
    """A retried POST would double-book time — see kb/quirks.md #12."""
    attempts = {"n": 0}

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        attempts["n"] += 1
        return FakeResponse(500, {"errorMessages": ["down"]}, url=url, method=method)

    client, _ = make_client(kb, handler, max_retries=5)
    with pytest.raises(ServerError):
        client.add_worklog(
            "CLOPSSEC-41456",
            started=datetime(2026, 4, 1, 10, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            duration_seconds=1800,
            comment="Daily",
        )
    assert attempts["n"] == 1


def test_malformed_json_is_reported_clearly(kb: KnowledgeBase) -> None:
    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(200, url=url, method=method, text="<html>not json</html>")

    client, _ = make_client(kb, handler)
    with pytest.raises(TrackspaceError, match="malformed JSON"):
        client.myself()


def test_timeouts_come_from_the_knowledge_base(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    client.myself()
    client.search_issues("jql", ["summary"])
    assert session.calls[0].timeout == kb.endpoint("myself").timeout_s
    assert session.calls[1].timeout == kb.endpoint("search").timeout_s


def test_unexpected_response_shapes_are_named(kb: KnowledgeBase) -> None:
    """A page that is not the expected envelope must fail with a readable message."""

    def list_handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(200, ["not", "an", "envelope"], url=url, method=method)

    client, _ = make_client(kb, list_handler)
    with pytest.raises(TrackspaceError, match="expected an object"):
        client.search_issues("jql", ["summary"])

    def wrong_payload(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(200, {"issues": "nope", "total": 1}, url=url, method=method)

    client, _ = make_client(kb, wrong_payload)
    with pytest.raises(TrackspaceError, match="not a list"):
        client.search_issues("jql", ["summary"])


def test_unreadable_total_ends_the_loop(kb: KnowledgeBase) -> None:
    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        return FakeResponse(
            200, {"issues": [{"key": "X-1", "fields": {}}], "total": "many"}, url=url, method=method
        )

    client, session = make_client(kb, handler)
    assert len(client.search_issues("jql", ["summary"])) == 1
    assert len(session.calls) == 1


# ---------------------------------------------------------------------------
# NT-2..NT-6 companion endpoints: issue, transitions, comments, attachments,
# issue links, remote links.
# ---------------------------------------------------------------------------


@dataclass
class MultipartFakeSession:
    """Like ``tests.conftest.FakeSession``, but also models the ``files`` and
    ``headers`` kwargs a real ``requests.Session.request`` accepts.

    The shared ``FakeSession`` never needed those before multipart uploads
    existed, and it is off-limits to edit (``tests/conftest.py`` is frozen for
    this task) — so this is a small, local stand-in used only by the upload
    tests below.
    """

    handler: Any
    headers: dict[str, Any] = field(default_factory=dict)
    auth: Any = None
    calls: list[Call] = field(default_factory=list)
    multipart: list[dict[str, Any]] = field(default_factory=list)
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
        self.multipart.append({"files": files, "headers": headers})
        return self.handler(method, url, params, json)

    def close(self) -> None:
        self.closed = True


def make_multipart_client(
    kb: KnowledgeBase, handler: Any, **kwargs: Any
) -> tuple[TrackspaceClient, MultipartFakeSession]:
    fake = MultipartFakeSession(handler=handler)
    client = TrackspaceClient(
        "test-token",
        kb=kb,
        session=fake,
        sleep=lambda _seconds: None,
        **kwargs,  # type: ignore[arg-type]
    )
    return client, fake


def test_issue_get_bare_and_with_changelog(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    issue = client.issue_get("CLOPSSEC-41456")
    assert issue["fields"]["summary"] == "Recurring meetings and internal coordination"
    assert session.calls[0].params is None

    client, session = make_client(kb, fixture_router(kb))
    issue = client.issue_get("CLOPSSEC-41456", expand="changelog")
    assert len(issue["changelog"]["histories"]) == 2
    assert session.calls[0].params == {"expand": "changelog"}


def test_issue_transitions_lists_the_fixture_transitions(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb))
    transitions = client.issue_transitions("CLOPSSEC-41456")
    assert [t["name"] for t in transitions] == ["Reopen"]


def test_comment_pagination_advances_by_returned_count(kb: KnowledgeBase) -> None:
    """The fixture has 3 comments; the fake server caps pages at 2 (SERVER_PAGE_CAP)."""
    client, session = make_client(kb, fixture_router(kb))
    comments = client.comments("CLOPSSEC-41456")
    assert [c["id"] for c in comments] == ["10100", "10101", "10102"]
    starts = [call.params["startAt"] for call in session.calls if call.params]
    assert starts == [0, 2]
    assert len(session.calls) == 2


def test_add_comment_is_never_retried(kb: KnowledgeBase) -> None:
    attempts = {"n": 0}

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        attempts["n"] += 1
        return FakeResponse(500, {"errorMessages": ["down"]}, url=url, method=method)

    client, _ = make_client(kb, handler, max_retries=5)
    with pytest.raises(ServerError):
        client.add_comment("CLOPSSEC-41456", "hello")
    assert attempts["n"] == 1


def test_update_comment_hits_put_on_the_comment_url(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    updated = client.update_comment("CLOPSSEC-41456", "10100", "edited")
    assert updated["id"] == "10200"
    assert session.calls[0].method == "PUT"
    assert session.calls[0].url.endswith("/issue/CLOPSSEC-41456/comment/10100")
    assert session.calls[0].json_body == {"body": "edited"}


def test_delete_comment_sends_delete_to_the_comment_url(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    assert client.delete_comment("CLOPSSEC-41456", "10100") is None
    assert session.calls[0].method == "DELETE"
    assert session.calls[0].url.endswith("/issue/CLOPSSEC-41456/comment/10100")


def test_upload_attachment_sends_multipart_and_the_token_header(
    kb: KnowledgeBase, tmp_path: Path
) -> None:
    client, session = make_multipart_client(kb, fixture_router(kb))
    path = tmp_path / "report.png"
    path.write_bytes(b"not really a png")

    created = client.upload_attachment("CLOPSSEC-41456", path)

    assert created["filename"] == "may-report.png"
    assert session.calls[0].method == "POST"
    assert session.calls[0].json_body is None  # no JSON body/content-type for a multipart POST
    sent = session.multipart[0]
    assert sent["files"] is not None and "file" in sent["files"]
    assert sent["headers"]["X-Atlassian-Token"] == "no-check"
    assert sent["headers"]["Content-Type"] is None  # tells requests to drop the JSON header


def test_upload_attachment_is_never_retried(kb: KnowledgeBase, tmp_path: Path) -> None:
    attempts = {"n": 0}

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        attempts["n"] += 1
        return FakeResponse(500, {"errorMessages": ["down"]}, url=url, method=method)

    client, _ = make_multipart_client(kb, handler, max_retries=5)
    path = tmp_path / "report.png"
    path.write_bytes(b"not really a png")
    with pytest.raises(ServerError):
        client.upload_attachment("CLOPSSEC-41456", path)
    assert attempts["n"] == 1


def test_attachment_meta_and_delete(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    meta = client.attachment_meta()
    assert meta == {"enabled": True, "uploadLimit": 10485760}

    assert client.delete_attachment("10500") is None
    assert session.calls[1].method == "DELETE"
    assert session.calls[1].url.endswith("/attachment/10500")


def test_link_types_lists_valid_names(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb))
    names = [t["name"] for t in client.link_types()]
    assert names == ["Blocks", "Cloners", "Duplicate", "Relates"]


def test_create_issue_link_is_never_retried(kb: KnowledgeBase) -> None:
    attempts = {"n": 0}

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        attempts["n"] += 1
        return FakeResponse(500, {"errorMessages": ["down"]}, url=url, method=method)

    client, _ = make_client(kb, handler, max_retries=5)
    with pytest.raises(ServerError):
        client.create_issue_link("Blocks", "CLOPSSEC-41501", "CLOPSSEC-41456")
    assert attempts["n"] == 1


def test_delete_issue_link_hits_delete_on_the_link_url(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    assert client.delete_issue_link("30001") is None
    assert session.calls[0].method == "DELETE"
    assert session.calls[0].url.endswith("/issueLink/30001")


def test_remote_links_lists_the_fixture_links(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb))
    links = client.remote_links("CLOPSSEC-41456")
    assert [link["id"] for link in links] == [20001, 20002]


def test_create_remote_link_sends_global_id_and_is_never_retried(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    created = client.create_remote_link(
        "CLOPSSEC-41456", "https://example.invalid/pr/1", "PR #1", global_id="system=pr-1"
    )
    assert created["id"] == 20001
    assert session.calls[0].json_body == {
        "object": {"url": "https://example.invalid/pr/1", "title": "PR #1"},
        "globalId": "system=pr-1",
    }

    attempts = {"n": 0}

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        attempts["n"] += 1
        return FakeResponse(500, {"errorMessages": ["down"]}, url=url, method=method)

    client, _ = make_client(kb, handler, max_retries=5)
    with pytest.raises(ServerError):
        client.create_remote_link("CLOPSSEC-41456", "https://example.invalid/pr/1", "PR #1")
    assert attempts["n"] == 1


def test_delete_remote_link_hits_delete_on_the_remote_link_url(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    assert client.delete_remote_link("CLOPSSEC-41456", "20001") is None
    assert session.calls[0].method == "DELETE"
    assert session.calls[0].url.endswith("/issue/CLOPSSEC-41456/remotelink/20001")


# ---------------------------------------------------------------------------
# Transition execution (ungated by the 2026-07-28 probe run)
# ---------------------------------------------------------------------------
def test_execute_transition_posts_the_transition_id(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    assert client.execute_transition("CLOPSSEC-41456", "831") is None
    assert session.calls[0].method == "POST"
    assert session.calls[0].url.endswith("/issue/CLOPSSEC-41456/transitions")
    assert session.calls[0].json_body == {"transition": {"id": "831"}}


def test_execute_transition_carries_an_optional_comment(kb: KnowledgeBase) -> None:
    client, session = make_client(kb, fixture_router(kb))
    client.execute_transition("CLOPSSEC-41456", "831", comment="reopening for follow-up")
    assert session.calls[0].json_body == {
        "transition": {"id": "831"},
        "update": {"comment": [{"add": {"body": "reopening for follow-up"}}]},
    }


def test_transition_post_is_never_retried(kb: KnowledgeBase) -> None:
    """A replayed transition moves the issue twice through a looping workflow."""
    attempts = {"n": 0}

    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        attempts["n"] += 1
        return FakeResponse(500, {"errorMessages": ["down"]}, url=url, method=method)

    client, _ = make_client(kb, handler, max_retries=5)
    with pytest.raises(ServerError):
        client.execute_transition("CLOPSSEC-41456", "831")
    assert attempts["n"] == 1
