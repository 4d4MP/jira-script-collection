"""The worklog dashboard, rendered in the terminal.

Same three panels the original drew with matplotlib — stacked daily hours by
ticket, top tickets grouped by title with IPs collapsed, and the summary block —
and the same numbers behind them. Only the canvas changed.
"""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

from trackspace.client import TrackspaceClient
from trackspace.errors import TrackspaceError
from trackspace.ui import charts, chrome, tables

from .clock import today_local
from .schedule import daterange

#: Bare IPv4 only — the scheduler's historical normaliser. The visualiser uses a
#: wider one; see kb/quirks.md #13 for why they stay different.
IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

#: Tickets drawn individually in the stacked chart; the rest collapse into one
#: "+N more" series (work_log.py:400-401).
VISIBLE_TICKETS = 8
#: Rows in the top-tickets panel (work_log.py:517).
TOP_TICKETS = 10
OTHER_SERIES = "__OTHER__"


def normalize_title(title: str) -> str:
    """Collapse IP-laden alert titles into one bucket."""
    return IP_PATTERN.sub("<IP>", title).strip()


@dataclass(frozen=True)
class WorklogRecord:
    """One worklog by the current user, inside the requested window."""

    key: str
    summary: str
    day: date
    hours: float
    comment: str


@dataclass(frozen=True)
class Identity:
    name: str
    key: str
    display_name: str


def _parse_started(raw: str) -> datetime:
    """Read a worklog ``started`` value.

    The strptime fallback drops the offset and is almost never reached on modern
    Python; it is kept because the proven code kept it (kb/quirks.md #6).
    """
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")  # noqa: DTZ007 - naive by design


def fetch_worklogs(
    client: TrackspaceClient,
    start: date,
    end: date,
    *,
    on_issues: Callable[[int], None] | None = None,
    on_issue: Callable[[int, int, str], None] | None = None,
    on_warning: Callable[[str], None] | None = None,
) -> tuple[list[WorklogRecord], Identity]:
    """Every worklog the token owner logged in ``[start, end]``.

    Two phases, because that is what the API supports: find the issues carrying
    the user's worklogs in the window, then read each issue's worklogs and filter
    by author and date client-side (kb/quirks.md #3, #4).
    """
    me = client.myself()
    identity = Identity(
        name=str(me.get("name") or me.get("key") or ""),
        key=str(me.get("key") or me.get("name") or ""),
        display_name=str(me.get("displayName") or me.get("name") or ""),
    )

    jql = client.kb.jql(
        "worklogs_by_current_user_in_range",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
    )
    issues = client.search_issues(jql, [client.kb.field_id("summary")])
    if on_issues is not None:
        on_issues(len(issues))

    records: list[WorklogRecord] = []
    for index, issue in enumerate(issues, 1):
        key = str(issue["key"])
        summary = str(issue.get("fields", {}).get("summary", ""))
        if on_issue is not None:
            on_issue(index, len(issues), key)
        try:
            worklogs = client.issue_worklogs(key, paginate=False)
        except TrackspaceError as exc:
            # One unreadable issue must not lose the rest of the period.
            if on_warning is not None:
                on_warning(f"failed {key}: {exc}")
            continue

        for worklog in worklogs:
            author = worklog.get("author") or {}
            author_name = str(author.get("name") or author.get("key") or "")
            if author_name not in (identity.name, identity.key):
                continue
            raw_started = worklog.get("started")
            if not isinstance(raw_started, str):
                continue
            try:
                started = _parse_started(raw_started)
            except ValueError:
                if on_warning is not None:
                    on_warning(f"{key}: unreadable worklog timestamp {raw_started!r}")
                continue
            day = started.date()
            if not (start <= day <= end):
                continue
            seconds = worklog.get("timeSpentSeconds")
            if not isinstance(seconds, (int, float)):
                if on_warning is not None:
                    on_warning(f"{key}: worklog without timeSpentSeconds, skipped")
                continue
            comment = worklog.get("comment", "")
            records.append(
                WorklogRecord(
                    key=key,
                    summary=summary,
                    day=day,
                    hours=seconds / 3600.0,
                    comment=comment if isinstance(comment, str) else "",
                )
            )
    return records, identity


@dataclass
class Dashboard:
    """Everything the panels need, aggregated once."""

    records: list[WorklogRecord]
    start: date
    end: date
    days: list[date]
    day_key_hours: dict[tuple[date, str], float]
    key_hours: dict[str, float]
    key_summary: dict[str, str]
    visible_keys: list[str]
    hidden_keys: list[str]

    @property
    def total_hours(self) -> float:
        return sum(record.hours for record in self.records)

    @property
    def day_totals(self) -> dict[date, float]:
        totals: dict[date, float] = dict.fromkeys(self.days, 0.0)
        for (day, _key), hours in self.day_key_hours.items():
            if day in totals:
                totals[day] += hours
        return totals

    @property
    def active_days(self) -> list[date]:
        return [day for day, hours in self.day_totals.items() if hours > 0.01]

    @property
    def average_active_day(self) -> float:
        totals = self.day_totals
        active = self.active_days
        return sum(totals[day] for day in active) / len(active) if active else 0.0


def aggregate(records: Sequence[WorklogRecord], start: date, end: date) -> Dashboard:
    day_key_hours: dict[tuple[date, str], float] = defaultdict(float)
    key_hours: dict[str, float] = defaultdict(float)
    key_summary: dict[str, str] = {}
    for record in records:
        day_key_hours[(record.day, record.key)] += record.hours
        key_hours[record.key] += record.hours
        key_summary[record.key] = record.summary

    ranked = sorted(key_hours, key=lambda key: key_hours[key], reverse=True)
    visible = ranked[:VISIBLE_TICKETS]
    hidden = ranked[VISIBLE_TICKETS:]
    return Dashboard(
        records=list(records),
        start=start,
        end=end,
        days=list(daterange(start, end)),
        day_key_hours=dict(day_key_hours),
        key_hours=dict(key_hours),
        key_summary=key_summary,
        visible_keys=visible,
        hidden_keys=hidden,
    )


def top_ticket_rows(records: Sequence[WorklogRecord]) -> list[tuple[str, float]]:
    """Top tickets grouped by normalised title, labelled as the original did."""
    group_hours: dict[str, float] = defaultdict(float)
    group_keys: dict[str, set[str]] = defaultdict(set)
    for record in records:
        title = normalize_title(record.summary) or record.key
        group_hours[title] += record.hours
        group_keys[title].add(record.key)

    ranked = sorted(group_hours.items(), key=lambda item: item[1], reverse=True)[:TOP_TICKETS]

    rows: list[tuple[str, float]] = []
    for title, hours in ranked:
        keys = group_keys[title]
        if len(keys) == 1:
            label = f"{next(iter(keys))}  ·  {title[:55]}"
        else:
            label = f"{title[:55]}  ({len(keys)} tickets)"
        if len(label) > 70:
            label = label[:67] + "..."
        rows.append((label, hours))
    return rows


def render(console: Console, data: Dashboard, *, username: str = "") -> None:
    """Print the three panels."""
    heading = f"Trackspace worklogs — {data.start:%d %b %Y} – {data.end:%d %b %Y}"
    if username:
        heading += f"      ·      {username}"
    console.print()
    console.print(Text(heading, style="heading"))

    if not data.records:
        tables.empty_notice(console, "No worklogs found in this period.")
        return

    _render_daily(console, data)
    console.print()
    _render_top_tickets(console, data)
    console.print()
    console.print(chrome.key_value_panel("Summary", _summary_rows(data), console))


def _render_daily(console: Console, data: Dashboard) -> None:
    series_order = [*data.visible_keys]
    labels = {key: key for key in series_order}
    if data.hidden_keys:
        series_order.append(OTHER_SERIES)
        labels[OTHER_SERIES] = f"+{len(data.hidden_keys)} more"

    palette = charts.series_palette(labels[key] for key in series_order)

    rows: list[tuple[str, dict[str, float]]] = []
    weekend_labels: list[str] = []
    today = today_local()
    for day in data.days:
        segments: dict[str, float] = {}
        for key in series_order:
            if key == OTHER_SERIES:
                hours = sum(data.day_key_hours.get((day, k), 0.0) for k in data.hidden_keys)
            else:
                hours = data.day_key_hours.get((day, key), 0.0)
            if hours > 0:
                segments[labels[key]] = hours
        label = f"{day:%a} {day.day:>2} {day:%b}"
        if day == today:
            label += " ←"
        if day.weekday() >= 5:
            weekend_labels.append(label)
        rows.append((label, segments))

    console.print(
        charts.stacked_bar_chart(
            rows,
            console=console,
            palette=palette,
            width=40,
            label_width=14,
            dim_rows=weekend_labels,
        )
    )
    average = data.average_active_day
    if average:
        console.print(Text(f"  avg active day: {average:.1f}h", style="muted"))
    console.print()
    console.print(
        charts.legend(
            palette,
            console=console,
            values={
                labels[key]: (
                    sum(data.key_hours[k] for k in data.hidden_keys)
                    if key == OTHER_SERIES
                    else data.key_hours[key]
                )
                for key in series_order
            },
        )
    )


def _render_top_tickets(console: Console, data: Dashboard) -> None:
    console.print(
        Text(
            "Top tickets in period      ·      grouped by title (IPs ignored)",
            style="heading",
        )
    )
    console.print(
        charts.bar_chart(
            top_ticket_rows(data.records),
            console=console,
            width=32,
            label_width=70,
        )
    )


def _summary_rows(data: Dashboard) -> list[tuple[str, str]]:
    day_totals = data.day_totals
    active = data.active_days
    weekdays_in_range = [day for day in data.days if day.weekday() < 5]
    unique_tickets = len({record.key for record in data.records})
    if day_totals:
        busiest = max(day_totals, key=lambda day: day_totals[day])
        busiest_text = f"{busiest:%a} {busiest.day} {busiest:%b} ({day_totals[busiest]:.1f}h)"
    else:
        busiest_text = "—"
    return [
        ("Total logged", f"{data.total_hours:.1f} h"),
        ("Days logged", f"{len(active)} / {len(weekdays_in_range)} weekdays"),
        ("Avg active day", f"{data.average_active_day:.1f} h"),
        ("Unique tickets", f"{unique_tickets}"),
        ("Busiest day", busiest_text),
    ]


def export(records: Sequence[WorklogRecord], path: Path) -> None:
    """Write the fetched worklogs to ``.json`` or ``.csv``.

    Only ever called when an explicit ``--export`` flag is given; the terminal
    rendering still prints alongside it.
    """
    rows: list[dict[str, Any]] = [
        {
            "key": record.key,
            "summary": record.summary,
            "date": record.day.isoformat(),
            "hours": round(record.hours, 3),
            "comment": record.comment,
        }
        for record in records
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["key", "summary", "date", "hours", "comment"]
            )
            writer.writeheader()
            writer.writerows(rows)
    else:
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
