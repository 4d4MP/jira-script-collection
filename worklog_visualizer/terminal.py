"""The report, rendered in the terminal.

Same three panels the matplotlib figure draws — daily hours stacked by ticket,
top tickets grouped by title with IPs collapsed, and the summary block — plus a
per-ticket table that a static image had no room for.

Long windows are bucketed rather than truncated: a year of daily rows would not
fit a screen, so days become weeks and then months, exactly as the figure thins
its x-axis labels.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pandas as pd
from rich.console import Console
from rich.text import Text

from trackspace.ui import charts, chrome, tables

from .fetch import normalize_title

#: Tickets drawn individually in the stacked chart; the rest collapse into one
#: "+N more" series. Matches the figure's legend cut-off.
VISIBLE_TICKETS = 8
#: Rows in the top-tickets panel.
TOP_TITLES = 10
#: Rows in the per-ticket table.
TOP_TICKET_ROWS = 15
OTHER_SERIES = "__OTHER__"

#: Bucket the timeline once a window outgrows one row per day.
DAILY_MAX_SPAN = 45
WEEKLY_MAX_SPAN = 400


@dataclass(frozen=True)
class Entry:
    """One worklog, flattened out of the DataFrame."""

    day: date
    ticket: str
    summary: str
    hours: float


def entries_from(df: pd.DataFrame) -> list[Entry]:
    """Flatten the DataFrame into plain records the renderers can hold."""
    if df.empty:
        return []
    entries: list[Entry] = []
    for record in df.to_dict(orient="records"):
        day = record["date"]
        if not isinstance(day, date):
            continue
        entries.append(
            Entry(
                day=day,
                ticket=str(record["ticket_id"]),
                summary=str(record["summary"]),
                hours=float(record["hours"]),
            )
        )
    return entries


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _bucket(day: date, span: int) -> tuple[str, str]:
    """``(bucket key, label)`` for a day, at the granularity the span needs."""
    if span <= DAILY_MAX_SPAN:
        return day.isoformat(), f"{day:%a} {day.day:>2} {day:%b}"
    if span <= WEEKLY_MAX_SPAN:
        monday = day - timedelta(days=day.weekday())
        return f"w{monday.isoformat()}", f"w/c {monday.day:>2} {monday:%b}"
    return f"{day.year}-{day.month:02d}", f"{day:%b %Y}"


def render_report(
    console: Console,
    df: pd.DataFrame,
    start_dt: datetime,
    end_dt: datetime,
    who: str,
    window_label: str,
) -> None:
    """Print the whole report."""
    entries = entries_from(df)
    start, end = start_dt.date(), end_dt.date()

    console.print()
    console.print(
        Text(
            f"Trackspace worklogs — {window_label}   ·   "
            f"{start_dt:%-d %b %Y %H:%M} – {end_dt:%-d %b %Y %H:%M}   ·   {who}",
            style="heading",
        )
    )

    if not entries:
        tables.empty_notice(console, f"No worklogs found in the {window_label}.")
        return

    _render_timeline(console, entries, start, end)
    console.print()
    _render_top_titles(console, entries)
    console.print()
    _render_tickets(console, entries)
    console.print()
    console.print(chrome.key_value_panel("Summary", summary_rows(entries, start, end), console))


def _render_timeline(console: Console, entries: Sequence[Entry], start: date, end: date) -> None:
    days = _days(start, end)
    span = len(days)

    ticket_hours: dict[str, float] = defaultdict(float)
    for entry in entries:
        ticket_hours[entry.ticket] += entry.hours
    ranked = sorted(ticket_hours, key=lambda key: ticket_hours[key], reverse=True)
    visible, hidden = ranked[:VISIBLE_TICKETS], ranked[VISIBLE_TICKETS:]

    labels = {ticket: ticket for ticket in visible}
    series_order = [*visible]
    if hidden:
        series_order.append(OTHER_SERIES)
        labels[OTHER_SERIES] = f"+{len(hidden)} more"
    hidden_set = set(hidden)

    # bucket key -> series label -> hours
    buckets: dict[str, dict[str, float]] = {}
    bucket_labels: dict[str, str] = {}
    for day in days:
        key, label = _bucket(day, span)
        buckets.setdefault(key, {})
        bucket_labels[key] = label
    for entry in entries:
        key, _ = _bucket(entry.day, span)
        if key not in buckets:  # a worklog outside the nominal window
            continue
        series = labels[OTHER_SERIES] if entry.ticket in hidden_set else entry.ticket
        buckets[key][series] = buckets[key].get(series, 0.0) + entry.hours

    ordered_keys = list(dict.fromkeys(_bucket(day, span)[0] for day in days))
    rows = [(bucket_labels[key], buckets[key]) for key in ordered_keys]
    weekend_labels = (
        [bucket_labels[_bucket(day, span)[0]] for day in days if day.weekday() >= 5]
        if span <= DAILY_MAX_SPAN
        else []
    )

    palette = charts.series_palette(labels[series] for series in series_order)
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

    totals = daily_totals(entries, days)
    active = [hours for hours in totals.values() if hours > 0]
    if active:
        average = sum(active) / len(active)
        shape = charts.sparkline([totals[day] for day in days], console=console)
        console.print(
            Text(f"  avg active day: {average:.1f}h", style="muted").append(
                f"    daily shape {shape}", style="muted"
            )
        )

    console.print()
    console.print(
        charts.legend(
            palette,
            console=console,
            values={
                labels[series]: (
                    sum(ticket_hours[ticket] for ticket in hidden)
                    if series == OTHER_SERIES
                    else ticket_hours[series]
                )
                for series in series_order
            },
        )
    )


def top_title_rows(entries: Sequence[Entry]) -> list[tuple[str, float]]:
    """Top groups by normalised title, labelled as the figure labels them."""
    group_hours: dict[str, float] = defaultdict(float)
    group_tickets: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        title = normalize_title(entry.summary)
        group_hours[title] += entry.hours
        if entry.ticket not in group_tickets[title]:
            group_tickets[title].append(entry.ticket)

    ranked = sorted(group_hours.items(), key=lambda item: item[1], reverse=True)[:TOP_TITLES]

    rows: list[tuple[str, float]] = []
    for title, hours in ranked:
        display = title or "(no title)"
        display = display[:50] + ("…" if len(display) > 50 else "")
        tickets = group_tickets[title]
        if len(tickets) > 1:
            display = f"{display}  ({len(tickets)} tickets)"
        else:
            display = f"{tickets[0]}  ·  {display}"
        rows.append((display, hours))
    return rows


def _render_top_titles(console: Console, entries: Sequence[Entry]) -> None:
    console.print(
        Text("Top tickets in period      ·      grouped by title (IPs ignored)", style="heading")
    )
    console.print(
        charts.bar_chart(top_title_rows(entries), console=console, width=32, label_width=70)
    )


def _render_tickets(console: Console, entries: Sequence[Entry]) -> None:
    hours: dict[str, float] = defaultdict(float)
    counts: dict[str, int] = defaultdict(int)
    summaries: dict[str, str] = {}
    first: dict[str, date] = {}
    last: dict[str, date] = {}
    for entry in entries:
        hours[entry.ticket] += entry.hours
        counts[entry.ticket] += 1
        summaries[entry.ticket] = entry.summary
        first[entry.ticket] = min(first.get(entry.ticket, entry.day), entry.day)
        last[entry.ticket] = max(last.get(entry.ticket, entry.day), entry.day)

    ranked = sorted(hours, key=lambda ticket: hours[ticket], reverse=True)
    shown = ranked[:TOP_TICKET_ROWS]
    caption = (
        f"{len(ranked)} tickets  ·  showing the top {len(shown)} by hours"
        if len(ranked) > len(shown)
        else f"{len(ranked)} tickets"
    )

    fixed = [18, 7, 7, 10, 10]
    tables.render_table(
        console,
        [
            tables.Column("Ticket", width=18),
            tables.Column("Summary", width=tables.flex_width(console, fixed)),
            tables.Column("Entries", width=7, justify="right"),
            tables.Column("Hours", width=7, justify="right"),
            tables.Column("First", width=10),
            tables.Column("Last", width=10),
        ],
        [
            (
                ticket,
                summaries[ticket],
                str(counts[ticket]),
                f"{hours[ticket]:.1f}",
                first[ticket].isoformat(),
                last[ticket].isoformat(),
            )
            for ticket in shown
        ],
        title="Tickets",
        caption=caption,
    )


def daily_totals(entries: Sequence[Entry], days: Sequence[date]) -> dict[date, float]:
    totals: dict[date, float] = dict.fromkeys(days, 0.0)
    for entry in entries:
        if entry.day in totals:
            totals[entry.day] += entry.hours
    return totals


def summary_rows(entries: Sequence[Entry], start: date, end: date) -> list[tuple[str, str]]:
    """The five figures the summary panel has always shown."""
    days = _days(start, end)
    totals = daily_totals(entries, days)
    active = [hours for hours in totals.values() if hours > 0]
    weekdays = [day for day in days if day.weekday() < 5]
    total = sum(entry.hours for entry in entries)
    busiest = max(totals, key=lambda day: totals[day]) if totals else None
    busiest_text = (
        f"{busiest:%a} {busiest.day} {busiest:%b} ({totals[busiest]:.1f}h)"
        if busiest is not None and totals[busiest] > 0
        else "—"
    )
    return [
        ("Total logged", f"{total:.1f} h"),
        ("Days logged", f"{len(active)} / {len(weekdays)} weekdays"),
        ("Avg active day", f"{(sum(active) / len(active) if active else 0):.1f} h"),
        ("Unique tickets", f"{len({entry.ticket for entry in entries})}"),
        ("Busiest day", busiest_text),
    ]
