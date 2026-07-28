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
from pathlib import Path
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
        files: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> requests.Response:
        # Additive: only build the multipart/header kwargs when a caller actually
        # needs them, so every pre-existing call site sends exactly the same
        # arguments it always has.
        request_kwargs: dict[str, Any] = {"params": params, "json": json_body, "timeout": timeout}
        if files is not None:
            request_kwargs["files"] = files
        if files is not None or extra_headers is not None:
            headers: dict[str, str | None] = dict(extra_headers or {})
            if files is not None:
                # A multipart body needs its own boundary-bearing Content-Type,
                # which requests sets itself. Setting the key to None tells
                # requests to drop the session's `application/json` header for
                # this one request instead of sending both.
                headers.setdefault("Content-Type", None)
            request_kwargs["headers"] = headers

        attempt = 0
        while True:
            error: TrackspaceError
            try:
                response = self.session.request(method, url, **request_kwargs)
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
        files: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        timeout: float | None = None,
        retry: bool | None = None,
    ) -> Any:
        """Call a knowledge-base endpoint and return the decoded JSON body.

        ``files`` (multipart) and ``extra_headers`` (merged into this one
        request, e.g. ``X-Atlassian-Token: no-check``) are additive — both
        default to ``None`` and every pre-existing call site is unaffected.
        """
        endpoint = self.kb.endpoint(endpoint_name)
        url = self._url(endpoint_name, **(path_params or {}))
        should_retry = endpoint.method == "GET" if retry is None else retry
        response = self._send(
            endpoint.method,
            url,
            params=params,
            json_body=json_body,
            files=files,
            extra_headers=extra_headers,
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

    # ---- issue, transitions, changelog -------------------------------------
    def issue_get(
        self,
        issue_key: str,
        *,
        expand: str | None = None,
        fields: str | None = None,
    ) -> dict[str, Any]:
        """One issue. ``expand="changelog"`` embeds history; ``fields`` scopes
        the response (e.g. ``"attachment"``) — there is no dedicated list route
        for either on Data Center."""
        params: dict[str, Any] = {}
        if expand:
            params["expand"] = expand
        if fields:
            params["fields"] = fields
        result = self.request_json(
            "issue_get", path_params={"issue_key": issue_key}, params=params or None
        )
        return cast(dict[str, Any], result or {})

    def issue_transitions(self, issue_key: str) -> list[dict[str, Any]]:
        """Transitions available on this issue from its current status.

        The list is a snapshot: it changes as the issue moves, so callers that
        intend to execute one must fetch it immediately beforehand rather than
        caching ids (``kb/trackspace.json`` → ``workflow.observed_transitions``).
        """
        data = self.request_json("issue_transitions", path_params={"issue_key": issue_key})
        if isinstance(data, dict) and isinstance(data.get("transitions"), list):
            return cast(list[dict[str, Any]], data["transitions"])
        return []

    def execute_transition(
        self, issue_key: str, transition_id: str, *, comment: str | None = None
    ) -> None:
        """Move an issue through one transition. Never retried.

        Like the worklog POST this is not idempotent, and unlike it a replay is
        not merely duplicative but wrong: by the time a retry lands the issue is
        already in the new status, so the same id either fails or — if the
        workflow loops — moves the issue a second time. A failed call surfaces
        as an ``ApiError`` and the caller decides.

        The instance answers 204 with no body, so there is nothing to return.
        """
        body: dict[str, Any] = {"transition": {"id": str(transition_id)}}
        if comment:
            body["update"] = {"comment": [{"add": {"body": comment}}]}
        self.request_json(
            "transition_execute",
            path_params={"issue_key": issue_key},
            json_body=body,
            retry=False,
        )

    # ---- comments ------------------------------------------------------------
    def comments(
        self,
        issue_key: str,
        *,
        page_size: int | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> list[dict[str, Any]]:
        """All comments on an issue, paged by returned count (mirrors
        ``iter_search_issues``: correct even if the server caps ``maxResults``
        below what was asked for)."""
        size = page_size or self.kb.page_size("comment_list")
        payload_key = self.kb.payload_key("comment_list")
        path_params = {"issue_key": issue_key}
        rows: list[dict[str, Any]] = []
        start_at = 0
        fetched = 0
        while True:
            data = self.request_json(
                "comment_list",
                path_params=path_params,
                params={"startAt": start_at, "maxResults": size},
            )
            page, total = self._page_of(data, payload_key, "comment_list")
            rows.extend(page)
            fetched += len(page)
            if on_progress is not None:
                on_progress(fetched, total)
            if not page or start_at + len(page) >= total:
                return rows
            start_at += len(page)

    def add_comment(self, issue_key: str, body: str) -> dict[str, Any]:
        """Post one comment. Never retried — not idempotent, same policy as
        ``add_worklog``."""
        result = self.request_json(
            "comment_add",
            path_params={"issue_key": issue_key},
            json_body={"body": body},
            retry=False,
        )
        return cast(dict[str, Any], result or {})

    def update_comment(self, issue_key: str, comment_id: str, body: str) -> dict[str, Any]:
        result = self.request_json(
            "comment_update",
            path_params={"issue_key": issue_key, "comment_id": comment_id},
            json_body={"body": body},
        )
        return cast(dict[str, Any], result or {})

    def delete_comment(self, issue_key: str, comment_id: str) -> None:
        self.request_json(
            "comment_delete", path_params={"issue_key": issue_key, "comment_id": comment_id}
        )

    # ---- attachments -----------------------------------------------------
    def upload_attachment(self, issue_key: str, path: Path) -> dict[str, Any]:
        """Upload a file as an attachment. Never retried — the POST is not
        idempotent, same policy as ``add_worklog``.

        Sent as multipart/form-data with the field named ``file`` and the
        ``X-Atlassian-Token: no-check`` header Jira Data Center requires for
        attachment uploads (both facts recorded on the ``attachment_upload``
        knowledge-base entry).
        """
        with path.open("rb") as handle:
            result = self.request_json(
                "attachment_upload",
                path_params={"issue_key": issue_key},
                files={"file": (path.name, handle)},
                extra_headers={"X-Atlassian-Token": "no-check"},
                retry=False,
            )
        if isinstance(result, list) and result:
            return cast(dict[str, Any], result[0])
        return cast(dict[str, Any], result or {})

    def attachment_meta(self) -> dict[str, Any]:
        """``{enabled, uploadLimit}`` — whether uploads are on and the byte cap."""
        result = self.request_json("attachment_meta")
        return cast(dict[str, Any], result or {})

    def delete_attachment(self, attachment_id: str) -> None:
        self.request_json("attachment_delete", path_params={"attachment_id": attachment_id})

    # ---- issue links and remote links --------------------------------------
    def link_types(self) -> list[dict[str, Any]]:
        """Valid ``type.name`` values for :meth:`create_issue_link`."""
        data = self.request_json("issue_link_types")
        if isinstance(data, dict) and isinstance(data.get("issueLinkTypes"), list):
            return cast(list[dict[str, Any]], data["issueLinkTypes"])
        return []

    def create_issue_link(
        self,
        type_name: str,
        inward_key: str,
        outward_key: str,
        *,
        comment: str | None = None,
    ) -> None:
        """Link two issues. Never retried — not idempotent."""
        body: dict[str, Any] = {
            "type": {"name": type_name},
            "inwardIssue": {"key": inward_key},
            "outwardIssue": {"key": outward_key},
        }
        if comment:
            body["comment"] = {"body": comment}
        self.request_json("issue_link_create", json_body=body, retry=False)

    def delete_issue_link(self, link_id: str) -> None:
        self.request_json("issue_link_delete", path_params={"link_id": link_id})

    def remote_links(self, issue_key: str) -> list[dict[str, Any]]:
        data = self.request_json("remote_link_list", path_params={"issue_key": issue_key})
        return cast(list[dict[str, Any]], data or [])

    def create_remote_link(
        self,
        issue_key: str,
        url: str,
        title: str,
        *,
        global_id: str | None = None,
    ) -> dict[str, Any]:
        """Attach a remote link. Never retried — not idempotent as a request,
        though a repeated ``global_id`` updates the same link server-side
        rather than duplicating it (see the ``remote_link_create`` KB entry)."""
        body: dict[str, Any] = {"object": {"url": url, "title": title}}
        if global_id:
            body["globalId"] = global_id
        result = self.request_json(
            "remote_link_create",
            path_params={"issue_key": issue_key},
            json_body=body,
            retry=False,
        )
        return cast(dict[str, Any], result or {})

    def delete_remote_link(self, issue_key: str, link_id: str) -> None:
        self.request_json(
            "remote_link_delete", path_params={"issue_key": issue_key, "link_id": link_id}
        )
