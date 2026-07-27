"""Transport behaviour: pagination, error mapping, retries, and what must not retry."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import requests

from tests.conftest import FakeResponse, fixture_router, make_client
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
