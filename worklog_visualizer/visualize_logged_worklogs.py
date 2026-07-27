"""Visualize the work YOU logged on Trackspace tickets across a rolling time
window ending at "now". Defaults to the last 30 days.

Authenticates against Trackspace using a Personal Access Token. No CSV input.

SETUP
-----
The script expects your Trackspace PAT in the TRACKSPACE_PAT env var:

    export TRACKSPACE_PAT="xxxxxxxxxxxxxxxxxxxx"

Defaults that you can override via env vars if needed:
    JIRA_BASE_URL  (default: https://trackspace.lhsystems.com)
    JIRA_AUTH_TYPE (default: bearer)
    JIRA_API_VERSION (default: 2  — Jira Server uses v2, Cloud can use 3)

USAGE
-----
    python -m worklog_visualizer                            # last 30 days
    python -m worklog_visualizer --ago 7d                   # last 7 days
    python -m worklog_visualizer --ago 4M                   # last 4 months
    python -m worklog_visualizer --ago 1Y --output yr.png   # last year
    python -m worklog_visualizer --date 2026_04_01-2026_04_30
    python -m worklog_visualizer --date 20260401-20260430
    python -m worklog_visualizer --datetime 2026_04_01-09:00-2026_04_01-17:30
    python -m worklog_visualizer --user colleague.name      # someone else

Three mutually exclusive ways to choose the time window (default: --ago 30d):

    --ago <N><unit>     rolling window ending at "now"
                        units (case-sensitive):
                            m = minutes, h = hours, d = days,
                            M = months,  Y = years
                        examples: 5m, 10h, 2d, 4M, 1Y

    --date <range>      absolute date range, inclusive of both endpoints.
                        accepts:
                            YYYY_MM_DD-YYYY_MM_DD   e.g. 2026_04_01-2026_04_30
                            YYYYMMDD-YYYYMMDD       e.g. 20260401-20260430

    --datetime <range>  absolute datetime range, minute precision.
                        format: YYYY_MM_DD-hh:mm-YYYY_MM_DD-hh:mm
                        e.g. 2026_04_01-09:00-2026_04_01-17:30

Absolute --date / --datetime values are interpreted in your local timezone.

OUTPUT
------
A multi-panel PNG (and an interactive window if a display is available)
showing daily totals stacked by ticket, top tickets of the period,
and summary stats — covering the requested time window.

NOTE ON SCOPE
-------------
This tool is deliberately unchanged in behaviour from the original script. Its
HTTP calls now go through ``trackspace.TrackspaceClient`` and its endpoints, JQL
and page sizes come from ``/kb``, but the arguments, the output, the PNG-first
rendering and every printed line are exactly as they were.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import re
import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
from matplotlib.figure import Figure
from matplotlib.patches import Patch

from trackspace.auth import read_pat
from trackspace.client import TrackspaceClient
from trackspace.errors import AuthError, ForbiddenError
from trackspace.kb import load_kb

# Default rolling window when no time-window flag is given.
DEFAULT_AGO = "30d"

USER_AGENT = "trackspace-worklog-visualizer"

# Absolute --date / --datetime values are interpreted in this timezone.
# Picks up whatever the OS thinks "local" is, which is what the user means
# when they type a wall-clock date or datetime.
LOCAL_TZ = datetime.now(UTC).astimezone().tzinfo


# ---------------------------------------------------------------------------
# --ago parsing
# ---------------------------------------------------------------------------

# Maps the unit suffix to (offset_kind, attr, singular_label).
#   td = datetime.timedelta, rd = dateutil.relativedelta.relativedelta
_AGO_UNITS: dict[str, tuple[str, str, str]] = {
    "m": ("td", "minutes", "minute"),
    "h": ("td", "hours", "hour"),
    "d": ("td", "days", "day"),
    "M": ("rd", "months", "month"),
    "Y": ("rd", "years", "year"),
}


def parse_ago(spec: str) -> tuple[Any, str]:
    """Parse '5m', '10h', '2d', '4M', '1Y' → (offset, human label).

    Units are case-sensitive: lowercase 'm' is minutes, uppercase 'M' is months.
    Returns either a timedelta (for m/h/d) or a relativedelta (for M/Y) so
    calendar arithmetic stays correct across month/year boundaries.
    """
    s = spec.strip()
    m = re.fullmatch(r"(\d+)\s*([a-zA-Z])", s)
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid --ago value: {spec!r}. "
            "expected <number><unit>, e.g. 5m, 10h, 2d, 4M, 1Y "
            "(units: m=minutes, h=hours, d=days, M=months, Y=years)"
        )
    n, unit = int(m.group(1)), m.group(2)
    if n <= 0:
        raise argparse.ArgumentTypeError(f"--ago value must be positive, got {spec!r}")
    if unit not in _AGO_UNITS:
        raise argparse.ArgumentTypeError(
            f"unknown unit {unit!r} in --ago value {spec!r}. "
            "valid units: m, h, d, M, Y (case-sensitive)"
        )
    kind, attr, label = _AGO_UNITS[unit]
    plural = "" if n == 1 else "s"
    human = f"{n} {label}{plural}"
    if kind == "td":
        return timedelta(**{attr: n}), human
    return relativedelta(**{attr: n}), human


# Two accepted shapes for --date.
_DATE_UNDERSCORE_RE = re.compile(r"^(\d{4})_(\d{2})_(\d{2})\s*-\s*(\d{4})_(\d{2})_(\d{2})$")
_DATE_COMPACT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})\s*-\s*(\d{4})(\d{2})(\d{2})$")


def parse_date_range(spec: str) -> tuple[date, date]:
    """Parse 'YYYY_MM_DD-YYYY_MM_DD' or 'YYYYMMDD-YYYYMMDD' → (start, end).

    Both endpoints are inclusive. Raises ArgumentTypeError on bad input.
    """
    s = spec.strip()
    for pattern in (_DATE_UNDERSCORE_RE, _DATE_COMPACT_RE):
        m = pattern.match(s)
        if m:
            try:
                start = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                end = date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
            except ValueError as e:
                raise argparse.ArgumentTypeError(f"invalid --date value: {spec!r}: {e}") from e
            if start > end:
                raise argparse.ArgumentTypeError(
                    f"--date: start {start.isoformat()} is after end {end.isoformat()}"
                )
            return start, end
    raise argparse.ArgumentTypeError(
        f"invalid --date value: {spec!r}. expected "
        "YYYY_MM_DD-YYYY_MM_DD or YYYYMMDD-YYYYMMDD "
        "(e.g. 2026_04_01-2026_04_30 or 20260401-20260430)"
    )


# YYYY_MM_DD-hh:mm-YYYY_MM_DD-hh:mm — three hyphens total, the middle one
# separates the two datetimes. Anchored regex resolves the ambiguity.
_DATETIME_RE = re.compile(
    r"^(\d{4})_(\d{2})_(\d{2})\s*-\s*(\d{2}):(\d{2})"
    r"\s*-\s*"
    r"(\d{4})_(\d{2})_(\d{2})\s*-\s*(\d{2}):(\d{2})$"
)


def parse_datetime_range(spec: str) -> tuple[datetime, datetime]:
    """Parse 'YYYY_MM_DD-hh:mm-YYYY_MM_DD-hh:mm' → (start, end), naive.

    Returned datetimes are timezone-naive; callers should attach LOCAL_TZ.
    Both endpoints are inclusive. Raises ArgumentTypeError on bad input.
    """
    s = spec.strip()
    m = _DATETIME_RE.match(s)
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid --datetime value: {spec!r}. expected "
            "YYYY_MM_DD-hh:mm-YYYY_MM_DD-hh:mm "
            "(e.g. 2026_04_01-09:00-2026_04_01-17:30)"
        )
    parts = list(map(int, m.groups()))
    try:
        start = datetime(parts[0], parts[1], parts[2], parts[3], parts[4])  # noqa: DTZ001
        end = datetime(parts[5], parts[6], parts[7], parts[8], parts[9])  # noqa: DTZ001
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid --datetime value: {spec!r}: {e}") from e
    if start > end:
        raise argparse.ArgumentTypeError(
            f"--datetime: start {start.isoformat()} is after end {end.isoformat()}"
        )
    return start, end


# ---------------------------------------------------------------------------
# Title normalisation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


def fetch_recent_worklogs(
    client: TrackspaceClient,
    user_identifier: str | None,
    start_dt: datetime,
    end_dt: datetime,
) -> pd.DataFrame:
    """Return a DataFrame with columns: date, ticket_id, summary, hours, author.

    Covers worklogs whose `started` instant lies in [start_dt, end_dt],
    using full datetime precision so sub-day windows (e.g. last 5 minutes)
    work correctly. start_dt and end_dt must be timezone-aware.
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
            raise SystemExit(
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
    target_label = (
        user_obj.get("displayName") or user_obj.get("emailAddress") or user_identifier or "you"
    )

    # JQL's worklogDate function only takes ISO dates (no time component),
    # so we widen to the calendar-day bounds and apply the precise datetime
    # filter in-loop after fetching individual worklog entries.
    start_date = start_dt.date()
    end_date = end_dt.date()
    jql = client.kb.jql(
        jql_pattern,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        **jql_extra,
    )
    print(
        f"→ Searching issues for {target_label} "
        f"({start_dt.isoformat(timespec='seconds')} → "
        f"{end_dt.isoformat(timespec='seconds')})...",
        file=sys.stderr,
    )
    issues = client.search_issues(jql, [client.kb.field_id("summary")])
    print(f"  found {len(issues)} candidate issue(s)", file=sys.stderr)

    rows: list[dict[str, Any]] = []
    for i, issue in enumerate(issues, 1):
        key = issue["key"]
        summary = issue["fields"].get("summary", "")
        print(f"  [{i}/{len(issues)}] fetching worklogs for {key}...", file=sys.stderr)
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
            wl_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            # Comparing tz-aware datetimes works regardless of source offset.
            if not (start_dt <= wl_dt <= end_dt):
                continue
            seconds = wl.get("timeSpentSeconds", 0)
            rows.append(
                {
                    "date": wl_dt.date(),
                    "ticket_id": key,
                    "summary": summary,
                    "hours": round(seconds / 3600, 3),
                    "author": author.get("displayName", ""),
                }
            )

    df = pd.DataFrame(rows, columns=["date", "ticket_id", "summary", "hours", "author"])
    return df


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def build_figure(
    df: pd.DataFrame, start_dt: datetime, end_dt: datetime, who: str, window_label: str
) -> Figure:
    """Three-panel figure: stacked daily bars + top tickets + summary."""
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.titleweight": "bold",
            "axes.titlesize": 12,
        }
    )

    start_date = start_dt.date()
    end_date = end_dt.date()
    span_days = (end_date - start_date).days + 1
    all_days = [start_date + timedelta(days=i) for i in range(span_days)]

    if df.empty:
        pivot = pd.DataFrame(0.0, index=all_days, columns=[])
    else:
        pivot = df.pivot_table(
            index="date", columns="ticket_id", values="hours", aggfunc="sum", fill_value=0
        ).reindex(all_days, fill_value=0)
        # Largest tickets at the bottom of the stack
        pivot = pivot[pivot.sum().sort_values(ascending=False).index]

    fig = plt.figure(figsize=(14, 8.5), constrained_layout=True)
    gs = fig.add_gridspec(2, 3, height_ratios=[1.6, 1])

    # --- Panel 1: daily stacked bars ---------------------------------------
    ax1 = fig.add_subplot(gs[0, :])
    cmap = plt.get_cmap("tab20")
    colors = [cmap(i % 20) for i in range(max(1, len(pivot.columns)))]

    x = np.arange(len(all_days))
    bottom = np.zeros(len(all_days))
    for ticket, color in zip(pivot.columns, colors, strict=False):
        vals = pivot[ticket].to_numpy()
        ax1.bar(x, vals, bottom=bottom, color=color, edgecolor="white", linewidth=0.6, label=ticket)
        bottom += vals

    # Shade weekends
    for i, d in enumerate(all_days):
        if d.weekday() >= 5:
            ax1.axvspan(i - 0.5, i + 0.5, color="#f4f4f4", zorder=0)

    # "Today" marker (end of window)
    if end_date in all_days:
        idx = all_days.index(end_date)
        ax1.axvline(idx, color="#d62728", linestyle="--", linewidth=1, alpha=0.7)

    # Reference line: average on active days only (zero days excluded)
    daily_totals = pivot.sum(axis=1).to_numpy() if not pivot.empty else np.array([0])
    active = daily_totals[daily_totals > 0]
    if len(active):
        avg = active.mean()
        ax1.axhline(avg, color="#555", linestyle=":", linewidth=1, alpha=0.7)
        ax1.text(
            len(all_days) - 0.5,
            avg,
            f"  avg active day: {avg:.1f}h",
            fontsize=8,
            color="#555",
            va="bottom",
            ha="right",
        )

    # X-axis: aim for ~25 visible labels max so long windows stay readable.
    # Always label the first tick, the last tick, and the 1st of any month.
    target_labels = 25
    step = max(1, span_days // target_labels)
    tick_labels = []
    for i, d in enumerate(all_days):
        if i == 0 or i == len(all_days) - 1 or d.day == 1:
            tick_labels.append(d.strftime("%-d %b"))
        elif i % step == 0:
            tick_labels.append(str(d.day))
        else:
            tick_labels.append("")
    ax1.set_xticks(x)
    ax1.set_xticklabels(
        tick_labels,
        fontsize=8,
        rotation=45 if span_days > 60 else 0,
        ha="right" if span_days > 60 else "center",
    )
    ax1.set_xlim(-0.6, len(all_days) - 0.4)
    ax1.set_ylabel("hours logged")
    title_range = f"{start_dt.strftime('%-d %b %Y %H:%M')} – {end_dt.strftime('%-d %b %Y %H:%M')}"
    ax1.set_title(
        f"Trackspace worklogs — {window_label}   ·   {title_range}   ·   {who}",
        loc="left",
    )
    ax1.grid(axis="y", linestyle="-", linewidth=0.5, alpha=0.3)
    ax1.set_axisbelow(True)

    # Compact legend (top N tickets)
    top_n = 8
    if len(pivot.columns):
        handles = [
            Patch(facecolor=colors[i], label=pivot.columns[i])
            for i in range(min(top_n, len(pivot.columns)))
        ]
        extra = len(pivot.columns) - top_n
        if extra > 0:
            handles.append(Patch(facecolor="#cccccc", label=f"+{extra} more"))
        ax1.legend(
            handles=handles,
            loc="upper left",
            bbox_to_anchor=(1.005, 1.0),
            frameon=False,
            fontsize=8,
            title="tickets",
            title_fontsize=8,
        )

    # --- Panel 2: top tickets bar (grouped by normalized title) -----------
    ax2 = fig.add_subplot(gs[1, :2])
    if df.empty:
        ax2.text(
            0.5,
            0.5,
            f"no worklogs found in the {window_label}",
            ha="center",
            va="center",
            color="#888",
        )
        ax2.set_axis_off()
    else:
        # Group by title with IPs collapsed, so the same alert type across
        # different source/dest IPs lands in a single bar.
        df_grouped = df.copy()
        df_grouped["title_key"] = df_grouped["summary"].apply(normalize_title)
        agg = (
            df_grouped.groupby("title_key")
            .agg(
                hours=("hours", "sum"),
                n_tickets=("ticket_id", "nunique"),
                sample_key=("ticket_id", "first"),
            )
            .sort_values("hours", ascending=True)
            .tail(10)
        )

        labels = []
        for title_key, row in agg.iterrows():
            display = str(title_key) if title_key else "(no title)"
            display = display[:50] + ("…" if len(display) > 50 else "")
            n = int(row["n_tickets"])
            display = f"{display}  ({n} tickets)" if n > 1 else f"{row['sample_key']}  ·  {display}"
            labels.append(display)

        ax2.barh(labels, agg["hours"].to_numpy(), color="#4c78a8", edgecolor="white")
        for i, v in enumerate(agg["hours"].to_numpy()):
            ax2.text(v, i, f"  {v:.1f}h", va="center", fontsize=8, color="#333")
        ax2.set_xlabel("hours")
        ax2.set_title(
            f"Top tickets — {window_label}   ·   grouped by title (IPs ignored)",
            loc="left",
        )
        ax2.grid(axis="x", linestyle="-", linewidth=0.5, alpha=0.3)
        ax2.set_axisbelow(True)

    # --- Panel 3: summary stats --------------------------------------------
    ax3 = fig.add_subplot(gs[1, 2])
    ax3.set_axis_off()

    total = float(df["hours"].sum()) if not df.empty else 0.0
    days_logged = int((pivot.sum(axis=1) > 0).sum()) if not pivot.empty else 0
    weekdays_in_window = sum(1 for d in all_days if d.weekday() < 5)
    avg_per_active = active.mean() if len(active) else 0
    busiest_idx = int(np.argmax(daily_totals)) if daily_totals.any() else None
    busiest = (
        f"{all_days[busiest_idx].strftime('%a %-d %b')} ({daily_totals[busiest_idx]:.1f}h)"
        if busiest_idx is not None and daily_totals[busiest_idx] > 0
        else "—"
    )

    lines = [
        ("Total logged", f"{total:.1f} h"),
        ("Days logged", f"{days_logged} / {weekdays_in_window} weekdays"),
        ("Avg active day", f"{avg_per_active:.1f} h"),
        ("Unique tickets", f"{df['ticket_id'].nunique() if not df.empty else 0}"),
        ("Busiest day", busiest),
    ]
    y = 0.95
    ax3.text(0.0, y, "Summary", fontsize=12, fontweight="bold", transform=ax3.transAxes)
    y -= 0.13
    for label, value in lines:
        ax3.text(0.0, y, label, fontsize=9, color="#666", transform=ax3.transAxes)
        ax3.text(1.0, y, value, fontsize=10, fontweight="bold", ha="right", transform=ax3.transAxes)
        y -= 0.13

    return fig


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--user", help="username of another user (default: yourself)")
    p.add_argument(
        "--output",
        default="jira_worklogs.png",
        help="output PNG path (default: jira_worklogs.png)",
    )
    p.add_argument(
        "--no-show", action="store_true", help="don't open the interactive matplotlib window"
    )

    # Three mutually exclusive ways to specify the time window.
    win = p.add_mutually_exclusive_group()
    win.add_argument(
        "--ago",
        help=(
            "rolling window ending at now. <number><unit>, "
            "units (case-sensitive): m=minutes, h=hours, d=days, "
            "M=months, Y=years. examples: 5m, 10h, 2d, 4M, 1Y. "
            f"(default if no window flag is given: {DEFAULT_AGO})"
        ),
    )
    win.add_argument(
        "--date",
        dest="date_range",
        help=(
            "absolute date range, both endpoints inclusive. "
            "format: YYYY_MM_DD-YYYY_MM_DD or YYYYMMDD-YYYYMMDD "
            "(e.g. 2026_04_01-2026_04_30 or 20260401-20260430). "
            "interpreted in local time."
        ),
    )
    win.add_argument(
        "--datetime",
        dest="datetime_range",
        help=(
            "absolute datetime range, both endpoints inclusive. "
            "format: YYYY_MM_DD-hh:mm-YYYY_MM_DD-hh:mm "
            "(e.g. 2026_04_01-09:00-2026_04_01-17:30). "
            "interpreted in local time."
        ),
    )
    return p.parse_args(argv)


def resolve_window(args: argparse.Namespace) -> tuple[datetime, datetime, str]:
    """Turn the parsed CLI flags into (start_dt, end_dt, window_label).

    Exactly one of --ago / --date / --datetime is honoured (argparse enforces
    mutual exclusion); when none is given, the default --ago window is used.
    """
    if args.datetime_range:
        sn, en = parse_datetime_range(args.datetime_range)
        start_dt = sn.replace(tzinfo=LOCAL_TZ)
        end_dt = en.replace(tzinfo=LOCAL_TZ)
        return start_dt, end_dt, "selected range"

    if args.date_range:
        sd, ed = parse_date_range(args.date_range)
        start_dt = datetime.combine(sd, time(0, 0, 0), tzinfo=LOCAL_TZ)
        end_dt = datetime.combine(ed, time(23, 59, 59), tzinfo=LOCAL_TZ)
        return start_dt, end_dt, "selected range"

    # Default / explicit --ago path.
    spec = args.ago or DEFAULT_AGO
    offset, ago_human = parse_ago(spec)
    end_dt = datetime.now(LOCAL_TZ)
    start_dt = end_dt - offset
    return start_dt, end_dt, f"last {ago_human}"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    kb = load_kb()
    base_url = os.environ.get("JIRA_BASE_URL", kb.base_url)
    email = os.environ.get("JIRA_EMAIL")
    # Prefer the project-specific PAT name, but fall back to the generic one.
    credentials = read_pat(kb=kb)
    auth_type = os.environ.get("JIRA_AUTH_TYPE", "bearer").lower()
    api_version = os.environ.get("JIRA_API_VERSION", kb.api_version)

    if credentials is None:
        print("ERROR: TRACKSPACE_PAT environment variable is not set.", file=sys.stderr)
        return 2

    client = TrackspaceClient(
        credentials.token,
        base_url=base_url,
        api_version=api_version,
        kb=kb,
        auth_type=auth_type,
        email=email,
        user_agent=USER_AGENT,
    )

    start_dt, end_dt, window_label = resolve_window(args)

    try:
        df = fetch_recent_worklogs(client, args.user, start_dt, end_dt)
        who = args.user or (client.myself().get("displayName") or "me")
    except AuthError:
        raise SystemExit("Auth failed (401). Check TRACKSPACE_PAT.") from None
    except ForbiddenError:
        raise SystemExit("Forbidden (403). Token lacks permission for this resource.") from None

    print(
        f"\n→ {len(df)} worklog entries totalling "
        f"{df['hours'].sum():.1f}h in the {window_label} "
        f"({start_dt.isoformat(timespec='seconds')} → "
        f"{end_dt.isoformat(timespec='seconds')})",
        file=sys.stderr,
    )

    fig = build_figure(df, start_dt, end_dt, who, window_label)
    out_path = Path(args.output).expanduser()
    fig.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"→ saved: {out_path}", file=sys.stderr)

    if not args.no_show:
        # A headless machine has no window to open; that must not fail the run.
        with contextlib.suppress(Exception):
            plt.show()
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
