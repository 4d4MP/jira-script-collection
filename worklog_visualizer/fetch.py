"""Fetching one user's worklogs over a window.

Two phases, because that is what the API supports: find the issues carrying the
user's worklogs, then read each issue's worklogs and filter by author and by
instant client-side (``kb/quirks.md`` #3, #4).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pandas as pd

from trackspace.client import TrackspaceClient
from trackspace.errors import TrackspaceError

#: ``(message)`` — progress for the live status line.
StatusCallback = Callable[[str], None]
#: ``(message)`` — something skipped, worth saying once.
WarningCallback = Callable[[str], None]

COLUMNS = ["date", "ticket_id", "summary", "hours", "author"]

# IPv4, optionally with /CIDR mask or :port.
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?::\d{1,5})?\b")
# IPv6 — covers the full form and any '::' compressed form.
# Uses negative lookarounds (instead of \b) because ':' isn't a word char,
# so we manually exclude hex/colon neighbours.
_IPV6_RE = re.compile(
    r"(?<![0-9a-fA-F:])"
    r"(?:"
    r"(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}"  # full / non-:: form
    r"|"
    r"(?:(?:[0-9a-fA-F]{1,4}:)*[0-9a-fA-F]{1,4})?"  # optional left part
    r"::"
    r"(?:(?:[0-9a-fA-F]{1,4}:)*[0-9a-fA-F]{1,4})?"  # optional right part
    r")"
    r"(?![0-9a-fA-F:])"
)


class UserNotFoundError(TrackspaceError):
    """``--user`` matched nobody on this instance."""


def normalize_title(title: str) -> str:
    """Collapse IP addresses in an alert title to '<IP>'.

    Lets us group tickets like 'Suspicious login from 192.168.1.10' and
    'Suspicious login from 10.0.0.5' into the same bar in the top-tickets
    panel — they're the same alert type, just different sources.
    """
    if not title:
        return ""
    out = _IPV4_RE.sub("<IP>", title)
    out = _IPV6_RE.sub("<IP>", out)
    # Collapse any whitespace fallout from substitution.
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _parse_started(raw: str) -> datetime | None:
    """A worklog ``started`` value, or ``None`` if it cannot be read."""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def fetch_recent_worklogs(
    client: TrackspaceClient,
    user_identifier: str | None,
    start_dt: datetime,
    end_dt: datetime,
    *,
    jql_filter: str | None = None,
    on_status: StatusCallback | None = None,
    on_warning: WarningCallback | None = None,
) -> tuple[pd.DataFrame, str]:
    """``(rows, whose worklogs these are)`` for the window.

    Covers worklogs whose ``started`` instant lies in ``[start_dt, end_dt]``,
    using full datetime precision so sub-day windows (e.g. the last 5 minutes)
    work correctly. ``start_dt`` and ``end_dt`` must be timezone-aware.

    ``jql_filter``, if given, replaces the usual ``worklogAuthor`` clause as the
    issue-selection filter: the issue search becomes ``(jql_filter) AND
    worklogDate >= ... AND worklogDate <= ...``. ``user_identifier`` still
    controls whose worklogs are *counted* — the client-side author filter (KB
    quirk #3) and the client-side instant filter (KB quirk #4) both still run
    against ``[start_dt, end_dt]`` exactly as when ``jql_filter`` is ``None``,
    since arbitrary caller-supplied JQL selects issues, not worklogs, and can't
    be trusted to scope worklogs correctly on its own.
    """
    start_ms = int(start_dt.timestamp() * 1000)

    # Resolve which user we're filtering on. Jira Server identifies users by
    # `name` / `key`; Cloud uses `accountId`. We collect any identifier we have
    # and accept a match against any of them. For --user, look the person up
    # via /user/search so the caller can pass username, email, or display name.
    if user_identifier is None:
        user_obj = client.myself()
        jql_pattern = "worklogs_by_current_user_in_range"
        jql_extra: dict[str, str] = {}
    else:
        found = client.find_user(user_identifier)
        if found is None:
            raise UserNotFoundError(
                f"No user found matching '{user_identifier}'. "
                "Try a username, email, or part of their display name."
            )
        user_obj = found
        canonical = user_obj.get("name") or user_obj.get("accountId")
        jql_pattern = "worklogs_by_named_user_in_range"
        jql_extra = {"username": str(canonical)}

    target_ids = {
        v.lower()
        for v in [
            user_obj.get("name"),
            user_obj.get("key"),
            user_obj.get("accountId"),
            user_obj.get("emailAddress"),
        ]
        if v
    }
    target_label = str(
        user_obj.get("displayName") or user_obj.get("emailAddress") or user_identifier or "you"
    )

    # JQL's worklogDate function only takes ISO dates (no time component),
    # so we widen to the calendar-day bounds and apply the precise datetime
    # filter in-loop after fetching individual worklog entries.
    start_date = start_dt.date().isoformat()
    end_date = end_dt.date().isoformat()
    if jql_filter is not None:
        # Caller-supplied JQL replaces the worklogAuthor clause but still gets
        # ANDed onto the same worklogDate bounds the KB template would use.
        search_jql = (
            f'({jql_filter}) AND worklogDate >= "{start_date}" AND worklogDate <= "{end_date}"'
        )
    else:
        search_jql = client.kb.jql(
            jql_pattern,
            start_date=start_date,
            end_date=end_date,
            **jql_extra,
        )
    if on_status is not None:
        on_status(f"Searching issues for {target_label}")
    issues = client.search_issues(search_jql, [client.kb.field_id("summary")])

    rows: list[dict[str, Any]] = []
    for i, issue in enumerate(issues, 1):
        key = str(issue["key"])
        summary = issue.get("fields", {}).get("summary", "")
        if on_status is not None:
            on_status(f"Fetching worklogs [{i}/{len(issues)}] {key}")
        for wl in client.issue_worklogs(key, started_after_ms=start_ms):
            author = wl.get("author", {})
            author_ids = {
                v.lower()
                for v in [
                    author.get("name"),
                    author.get("key"),
                    author.get("accountId"),
                    author.get("emailAddress"),
                ]
                if v
            }
            if not (author_ids & target_ids):
                continue
            started = wl.get("started")  # e.g. 2026-04-15T09:30:00.000+0000
            if not started:
                continue
            wl_dt = _parse_started(started)
            if wl_dt is None:
                if on_warning is not None:
                    on_warning(f"{key}: unreadable worklog timestamp {started!r}, skipped")
                continue
            # Comparing tz-aware datetimes works regardless of source offset.
            if not (start_dt <= wl_dt <= end_dt):
                continue
            seconds = wl.get("timeSpentSeconds", 0)
            if not isinstance(seconds, (int, float)):
                if on_warning is not None:
                    on_warning(f"{key}: worklog without timeSpentSeconds, skipped")
                continue
            rows.append(
                {
                    "date": wl_dt.date(),
                    "ticket_id": key,
                    "summary": summary,
                    "hours": round(seconds / 3600, 3),
                    "author": author.get("displayName", ""),
                }
            )

    return pd.DataFrame(rows, columns=COLUMNS), target_label
