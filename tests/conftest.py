"""A fake Trackspace served entirely from ``kb/fixtures``. No network, no PAT."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlparse

import pytest

from trackspace.client import TrackspaceClient
from trackspace.kb import KnowledgeBase, load_kb

#: The fake server never returns more than this many rows for an explicitly
#: paginated request, however large a maxResults the client asks for. Mirrors a
#: server capping maxResults — the case the returned-count pager must survive.
SERVER_PAGE_CAP = 2

ISSUE_WORKLOG_FIXTURES = {
    "CLOPSSEC-41456": "issue_worklog_CLOPSSEC-41456",
    "CLOPSSEC-41501": "issue_worklog_CLOPSSEC-41501",
    "CLOPSSEC-41502": "issue_worklog_CLOPSSEC-41502",
    "CLOPSSEC-41677": "issue_worklog_CLOPSSEC-41677",
    "CLOPSSEC-41703": "issue_worklog_CLOPSSEC-41703",
}


class FakeRequest:
    def __init__(self, method: str) -> None:
        self.method = method


class FakeResponse:
    """Just enough of ``requests.Response`` for the client and error mapping."""

    def __init__(
        self,
        status_code: int = 200,
        payload: Any = None,
        *,
        headers: dict[str, str] | None = None,
        url: str = "https://trackspace.lhsystems.com/rest/api/2/test",
        method: str = "GET",
        text: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.request = FakeRequest(method)
        self._payload = payload
        self.text = text if text is not None else ("" if payload is None else json.dumps(payload))

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")

    def json(self) -> Any:
        if self._payload is None:
            return json.loads(self.text)
        return self._payload


@dataclass
class Call:
    method: str
    url: str
    params: dict[str, Any] | None
    json_body: dict[str, Any] | None
    timeout: float | None


@dataclass
class FakeSession:
    """Stands in for ``requests.Session``."""

    handler: Callable[[str, str, dict[str, Any] | None, dict[str, Any] | None], FakeResponse]
    headers: dict[str, str] = field(default_factory=dict)
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
    ) -> FakeResponse:
        self.calls.append(Call(method, url, params, json, timeout))
        return self.handler(method, url, params, json)

    def close(self) -> None:
        self.closed = True

    def paths(self) -> list[str]:
        return [urlparse(call.url).path for call in self.calls]


def _page(payload: dict[str, Any], key: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """Slice a fixture the way a paginating server would."""
    rows = payload[key]
    if not params or "startAt" not in params:
        return payload
    start = int(params.get("startAt", 0))
    size = min(int(params.get("maxResults", 20)), SERVER_PAGE_CAP)
    return {
        "startAt": start,
        "maxResults": size,
        "total": len(rows),
        key: rows[start : start + size],
    }


def fixture_router(
    kb: KnowledgeBase,
    *,
    failing_issues: frozenset[str] = frozenset(),
    empty_search: bool = False,
) -> Callable[[str, str, dict[str, Any] | None, dict[str, Any] | None], FakeResponse]:
    """Route a request onto the fixture that answers it."""

    def handler(
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
    ) -> FakeResponse:
        path = urlparse(url).path

        if path.endswith("/myself"):
            return FakeResponse(200, kb.fixture("myself"), url=url, method=method)

        if path.endswith("/user/search"):
            query = str((params or {}).get("username", ""))
            name = "user_search_empty" if query == "nobody" else "user_search"
            return FakeResponse(200, kb.fixture(name), url=url, method=method)

        if path.endswith("/search"):
            if empty_search:
                return FakeResponse(200, kb.fixture("search_empty"), url=url, method=method)
            start = int((params or {}).get("startAt", 0))
            name = "search_worklog_authors_page1" if start == 0 else "search_worklog_authors_page2"
            return FakeResponse(200, kb.fixture(name), url=url, method=method)

        match = re.search(r"/issue/([^/]+)/worklog", path)
        if match:
            key = unquote(match.group(1))
            if method == "POST":
                return FakeResponse(201, kb.fixture("add_worklog_created"), url=url, method=method)
            if key in failing_issues:
                return FakeResponse(
                    500, kb.fixture("errors")["500"]["body"], url=url, method=method
                )
            fixture_name = ISSUE_WORKLOG_FIXTURES.get(key, "issue_worklog_empty")
            return FakeResponse(
                200, _page(kb.fixture(fixture_name), "worklogs", params), url=url, method=method
            )

        return FakeResponse(404, kb.fixture("errors")["404"]["body"], url=url, method=method)

    return handler


@pytest.fixture(scope="session")
def kb() -> KnowledgeBase:
    return load_kb()


@pytest.fixture
def session(kb: KnowledgeBase) -> FakeSession:
    return FakeSession(handler=fixture_router(kb))


@pytest.fixture
def client(kb: KnowledgeBase, session: FakeSession) -> TrackspaceClient:
    return TrackspaceClient(
        "test-token",
        kb=kb,
        session=session,  # type: ignore[arg-type]
        sleep=lambda _seconds: None,
    )


def make_client(
    kb: KnowledgeBase,
    handler: Callable[..., FakeResponse],
    **kwargs: Any,
) -> tuple[TrackspaceClient, FakeSession]:
    fake = FakeSession(handler=handler)
    return (
        TrackspaceClient(
            "test-token",
            kb=kb,
            session=fake,  # type: ignore[arg-type]
            sleep=lambda _seconds: None,
            **kwargs,
        ),
        fake,
    )
