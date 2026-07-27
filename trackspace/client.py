"""HTTP client for Trackspace's Jira Data Center REST API.

Every endpoint path, page size, timeout and JQL string comes from the knowledge
base — this module contributes the transport behaviour the original scripts never
had: retries, typed errors, and a pager that cannot silently skip rows.

One deliberate asymmetry: **GETs retry, worklog POSTs do not.** Posting a worklog
is not idempotent and nothing dedupes, so a retry of a request that actually
landed double-books the time. See ``kb/quirks.md`` #12.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from typing import Any, cast

import requests
from requests.auth import HTTPBasicAuth

from .errors import (
    ApiError,
    ConfigurationError,
    TrackspaceError,
    TransportError,
    error_for_response,
)
from .kb import KnowledgeBase, load_kb

#: Progress callback: ``(fetched_so_far, total_reported_by_server)``.
ProgressCallback = Callable[[int, int], None]

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_S = 0.5
#: Never sleep longer than this between retries, whatever Retry-After claims.
MAX_BACKOFF_S = 30.0


class TrackspaceClient:
    """A session against one Trackspace instance, authenticated with a PAT."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str | None = None,
        api_version: str | None = None,
        kb: KnowledgeBase | None = None,
        session: requests.Session | None = None,
        auth_type: str = "bearer",
        email: str | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_s: float = DEFAULT_BACKOFF_BASE_S,
        sleep: Callable[[float], None] = time.sleep,
        user_agent: str | None = None,
    ) -> None:
        self.kb = kb or load_kb()
        self.base_url = (base_url or self.kb.base_url).rstrip("/")
        self.api_version = api_version or self.kb.api_version
        self.api_root = self.kb.api_root_for(self.api_version)
        self._max_retries = max(0, max_retries)
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep

        self.session = session if session is not None else requests.Session()
        self.session.headers.update(self.kb.request_headers)
        if user_agent:
            self.session.headers["User-Agent"] = user_agent

        if auth_type == "bearer":
            self.session.headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "basic":
            if not email:
                raise ConfigurationError("JIRA_EMAIL is required for basic auth.")
            self.session.auth = HTTPBasicAuth(email, token)
        else:
            raise ConfigurationError(f"unknown auth type {auth_type!r}; use 'bearer' or 'basic'")

    # ---- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> TrackspaceClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---- transport ---------------------------------------------------------
    def _url(self, endpoint_name: str, **path_params: str) -> str:
        endpoint = self.kb.endpoint(endpoint_name)
        return f"{self.base_url}{self.api_root}{endpoint.render(self.api_root, **path_params)}"

    def _retry_delay(self, error: TrackspaceError, attempt: int) -> float:
        retry_after = getattr(error, "retry_after", None)
        if isinstance(retry_after, (int, float)):
            return min(float(retry_after), MAX_BACKOFF_S)
        return min(self._backoff_base_s * (2.0**attempt), MAX_BACKOFF_S)

    @staticmethod
    def _is_retryable(error: TrackspaceError) -> bool:
        if isinstance(error, TransportError):
            return True
        return isinstance(error, ApiError) and error.retryable

    def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        timeout: float,
        retry: bool,
    ) -> requests.Response:
        attempt = 0
        while True:
            error: TrackspaceError
            try:
                response = self.session.request(
                    method, url, params=params, json=json_body, timeout=timeout
                )
            except requests.Timeout:
                error = TransportError(f"{method} {url} timed out after {timeout:g}s", url=url)
            except requests.RequestException as exc:
                error = TransportError(f"{method} {url} failed: {exc}", url=url)
            else:
                if response.status_code < 400:
                    return response
                error = error_for_response(response)

            if retry and attempt < self._max_retries and self._is_retryable(error):
                self._sleep(self._retry_delay(error, attempt))
                attempt += 1
                continue
            raise error

    def request_json(
        self,
        endpoint_name: str,
        *,
        path_params: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
        retry: bool | None = None,
    ) -> Any:
        """Call a knowledge-base endpoint and return the decoded JSON body."""
        endpoint = self.kb.endpoint(endpoint_name)
        url = self._url(endpoint_name, **(path_params or {}))
        should_retry = endpoint.method == "GET" if retry is None else retry
        response = self._send(
            endpoint.method,
            url,
            params=params,
            json_body=json_body,
            timeout=timeout if timeout is not None else endpoint.timeout_s,
            retry=should_retry,
        )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise TrackspaceError(f"malformed JSON in the response to {url}") from exc

    def _page_of(self, data: Any, payload_key: str, endpoint_name: str) -> tuple[list[Any], int]:
        """``(rows, total)`` from a paginated envelope, or a clear error.

        A page that is not an object at all means the endpoint answered with
        something this client does not understand — better to say so than to
        raise ``AttributeError`` three frames later.
        """
        if not isinstance(data, dict):
            raise TrackspaceError(
                f"unexpected response shape from {endpoint_name}: "
                f"expected an object, got {type(data).__name__}"
            )
        rows = data.get(payload_key) or []
        if not isinstance(rows, list):
            raise TrackspaceError(
                f"unexpected response shape from {endpoint_name}: "
                f"{payload_key!r} is {type(rows).__name__}, not a list"
            )
        try:
            total = int(data.get("total", 0) or 0)
        except (TypeError, ValueError):
            # A missing or unreadable `total` simply ends the loop after this page.
            total = 0
        return list(rows), total

    # ---- endpoints ---------------------------------------------------------
    def myself(self) -> dict[str, Any]:
        """The token owner. Identity keys: ``name``, ``key``, ``displayName``."""
        return cast(dict[str, Any], self.request_json("myself"))

    def find_user(self, query: str) -> dict[str, Any] | None:
        """First user matching a username, email or partial display name.

        Data Center answers with a bare array; the ``{"values": [...]}`` envelope
        is accepted too because ``work.py`` accepted it.
        """
        data = self.request_json("user_search", params={"username": query, "maxResults": 2})
        if isinstance(data, list) and data:
            return cast(dict[str, Any], data[0])
        if isinstance(data, dict) and data.get("values"):
            return cast(dict[str, Any], data["values"][0])
        return None

    def iter_search_issues(
        self,
        jql: str,
        fields: Sequence[str],
        *,
        page_size: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Page through ``GET /search``, advancing by the number of rows returned.

        Advancing by the returned count rather than the requested page size is the
        safer of the two strategies the original scripts used: it stays correct if
        the server caps ``maxResults`` below what was asked for.
        """
        size = page_size or self.kb.page_size("search")
        payload_key = self.kb.payload_key("search")
        start_at = 0
        fetched = 0
        while True:
            data = self.request_json(
                "search",
                params={
                    "jql": jql,
                    "fields": ",".join(fields),
                    "startAt": start_at,
                    "maxResults": size,
                },
            )
            page, total = self._page_of(data, payload_key, "search")
            yield from page
            fetched += len(page)
            if on_progress is not None:
                on_progress(fetched, total)
            # An empty page means the server has nothing more, whatever `total` says.
            if not page or start_at + len(page) >= total:
                return
            start_at += len(page)

    def search_issues(
        self,
        jql: str,
        fields: Sequence[str],
        *,
        page_size: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        return list(
            self.iter_search_issues(jql, fields, page_size=page_size, on_progress=on_progress)
        )

    def issue_worklogs(
        self,
        issue_key: str,
        *,
        started_after_ms: int | None = None,
        page_size: int | None = None,
        paginate: bool = True,
    ) -> list[dict[str, Any]]:
        """Worklogs on one issue — **all authors**, so callers must filter.

        ``paginate=False`` reproduces the scheduler's single unparameterised
        request (``work_log.py:306-308``).
        """
        payload_key = self.kb.payload_key("issue_worklogs")
        path_params = {"issue_key": issue_key}

        if not paginate:
            data = self.request_json("issue_worklogs", path_params=path_params)
            page, _total = self._page_of(data, payload_key, "issue_worklogs")
            return page

        size = page_size or self.kb.page_size("issue_worklogs")
        worklogs: list[dict[str, Any]] = []
        start_at = 0
        while True:
            params: dict[str, Any] = {"startAt": start_at, "maxResults": size}
            if started_after_ms is not None:
                params["startedAfter"] = started_after_ms
            data = self.request_json("issue_worklogs", path_params=path_params, params=params)
            page, total = self._page_of(data, payload_key, "issue_worklogs")
            worklogs.extend(page)
            if not page or start_at + len(page) >= total:
                return worklogs
            start_at += len(page)

    def add_worklog(
        self,
        issue_key: str,
        *,
        started: datetime,
        duration_seconds: int,
        comment: str,
    ) -> dict[str, Any]:
        """Post one worklog. Never retried — see the module docstring."""
        body = {
            "timeSpentSeconds": duration_seconds,
            "started": started.strftime(self.kb.worklog_started_format),
            "comment": comment,
        }
        result = self.request_json(
            "add_worklog",
            path_params={"issue_key": issue_key},
            json_body=body,
            retry=False,
        )
        return cast(dict[str, Any], result or {})
