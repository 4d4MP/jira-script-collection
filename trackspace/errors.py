"""Error types for every failure mode a Trackspace call can hit.

The message format of :class:`ApiError` is deliberate: ``HTTP {status}: {body}``
truncated to 200 characters is exactly what the original worklog poster printed
for a failed submit (``work_log.py:249``), and the scheduler still reproduces
that line verbatim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import requests

#: How much of an error body is ever surfaced. Matches ``work_log.py:249``.
BODY_SNIPPET_CHARS = 200


class TrackspaceError(Exception):
    """Base class for everything this library raises."""


class ConfigurationError(TrackspaceError):
    """The tool cannot run as configured — missing PAT, bad base URL, no TTY."""


class TransportError(TrackspaceError):
    """The request never produced an HTTP response: timeout, DNS, reset TLS."""

    def __init__(self, message: str, *, url: str = "") -> None:
        super().__init__(message)
        self.url = url


class ApiError(TrackspaceError):
    """Trackspace answered with a non-2xx status."""

    def __init__(
        self,
        status: int,
        body: str = "",
        *,
        url: str = "",
        method: str = "GET",
    ) -> None:
        self.status = status
        self.body = body
        self.snippet = body[:BODY_SNIPPET_CHARS]
        self.url = url
        self.method = method
        super().__init__(f"HTTP {status}: {self.snippet}")

    @property
    def retryable(self) -> bool:
        return False


class AuthError(ApiError):
    """401 — the PAT is missing, malformed or expired."""


class ForbiddenError(ApiError):
    """403 — the PAT is valid but not permitted to touch this resource."""


class NotFoundError(ApiError):
    """404 — unknown issue key, or an issue the token cannot see."""


class RateLimitError(ApiError):
    """429 — inferred, never observed on this instance. See kb/quirks.md #15."""

    def __init__(
        self,
        status: int,
        body: str = "",
        *,
        url: str = "",
        method: str = "GET",
        retry_after: float | None = None,
    ) -> None:
        super().__init__(status, body, url=url, method=method)
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return True


class ServerError(ApiError):
    """5xx — Trackspace itself failed."""

    @property
    def retryable(self) -> bool:
        return True


def _parse_retry_after(value: str | None) -> float | None:
    """Read a ``Retry-After`` header expressed in seconds.

    The HTTP-date form is not handled: this instance has never been seen to send
    a 429 at all, so a date-form header would be pure speculation. Returning
    ``None`` simply falls back to exponential backoff.
    """
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def error_for_response(response: requests.Response) -> ApiError:
    """Map a non-2xx response onto the matching :class:`ApiError` subclass."""
    status = response.status_code
    url = response.url or ""
    method = (response.request.method if response.request is not None else "GET") or "GET"
    try:
        body = response.text or ""
    except Exception:  # pragma: no cover - only reachable on a broken stream
        body = ""

    if status == 401:
        return AuthError(status, body, url=url, method=method)
    if status == 403:
        return ForbiddenError(status, body, url=url, method=method)
    if status == 404:
        return NotFoundError(status, body, url=url, method=method)
    if status == 429:
        return RateLimitError(
            status,
            body,
            url=url,
            method=method,
            retry_after=_parse_retry_after(response.headers.get("Retry-After")),
        )
    if status >= 500:
        return ServerError(status, body, url=url, method=method)
    return ApiError(status, body, url=url, method=method)
