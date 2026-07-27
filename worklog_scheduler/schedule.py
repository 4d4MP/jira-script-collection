"""Turning a schedule into worklog entries.

The rules are inherited exactly from the original tool (``work_log.py:177-217``):

* recurring meetings fire on every matching weekday inside the range;
* excluded dates suppress **both** recurring and one-off entries;
* one-offs outside the range are skipped but stay in the config;
* entries come out sorted by start instant;
* weekends are not special-cased — they are empty only because the default
  recurring meetings are Mon-Fri. See ``kb/quirks.md`` #9.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from trackspace.errors import ConfigurationError

from .clock import today_local
from .config import WEEKDAY_NAMES, OneOffMeeting, RecurringMeeting, ScheduleConfig

_WEEKDAY_LOOKUP = {name.upper(): index for index, name in enumerate(WEEKDAY_NAMES)}

#: SC-17: plain minutes (unchanged), or Nh / NhMm / Mm as sugar for it. Order
#: matters — the longest, most specific alternative (hours-and-minutes) is
#: tried first so "1h30m" isn't left partially matched by the bare-hours arm.
#: Every alternative requires at least one digit, so bare "h"/"m" or an empty
#: duration still fail to match at all, and something like "1h5" or "90x" has
#: no alternative that consumes it fully up to the following "=".
_DURATION_GROUP = r"(?P<duration>\d+h\d+m|\d+h|\d+m|\d+)"

#: DAYS[/EVERY_N_WEEKS][~ANCHOR]@HH:MM+MINUTES=COMMENT
_RECURRING_SPEC = re.compile(
    r"^(?P<days>[A-Za-z0-9,\-]+)"
    r"(?:/(?P<interval>\d+))?"
    r"(?:~(?P<anchor>\d{4}-\d{2}-\d{2}))?"
    r"@(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"\+" + _DURATION_GROUP + r"=(?P<comment>.+)$"
)
_ONEOFF_SPEC = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})@(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"\+" + _DURATION_GROUP + r"=(?P<comment>.+)$"
)

_DURATION_SUGAR = re.compile(r"^(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?$")


def _duration_minutes(text: str) -> int:
    """Read the ``duration`` capture group: plain digits, or Nh/NhMm/Mm sugar."""
    if text.isdigit():
        return int(text)
    match = _DURATION_SUGAR.match(text)
    if match is None:
        # Unreachable in practice: _RECURRING_SPEC/_ONEOFF_SPEC's duration
        # group only ever captures one of the four shapes this also accepts.
        raise ConfigurationError(f"unreadable duration {text!r}")
    hours = int(match["hours"] or 0)
    minutes = int(match["minutes"] or 0)
    return hours * 60 + minutes


@dataclass(frozen=True)
class WorklogEntry:
    """One worklog that would be posted."""

    day: date
    started: datetime
    duration_min: int
    comment: str
    #: SC-18: which rule produced this entry, e.g. "recurring #1 (Daily)" or
    #: "one-off". Empty unless the caller asked for it — nothing populates this
    #: outside build_entries, and no pinned test compares WorklogEntry equality.
    source: str = ""


def daterange(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def build_entries(cfg: ScheduleConfig) -> list[WorklogEntry]:
    """Expand the configured schedule into the entries it implies."""
    try:
        tz = ZoneInfo(cfg.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigurationError(f"unknown timezone {cfg.timezone!r}") from exc
    try:
        start = date.fromisoformat(cfg.start_date)
        end = date.fromisoformat(cfg.end_date)
    except ValueError as exc:
        raise ConfigurationError(f"bad date in the schedule: {exc}") from exc

    excluded = set()
    for value in cfg.exclude_dates:
        try:
            excluded.add(date.fromisoformat(value))
        except ValueError as exc:
            raise ConfigurationError(f"bad excluded date {value!r}: {exc}") from exc

    entries: list[WorklogEntry] = []

    # Every-N-weeks meetings count from their own anchor week when they have one,
    # and from the start of the range when they do not.
    anchor_weeks: list[date] = []
    for meeting in cfg.recurring:
        if meeting.anchor:
            try:
                anchor = date.fromisoformat(meeting.anchor)
            except ValueError as exc:
                raise ConfigurationError(
                    f"bad anchor date {meeting.anchor!r} on {meeting.comment!r}: {exc}"
                ) from exc
        else:
            anchor = start
        anchor_weeks.append(anchor - timedelta(days=anchor.weekday()))

    for day in daterange(start, end):
        if day in excluded:
            continue
        for index, (meeting, anchor_week) in enumerate(
            zip(cfg.recurring, anchor_weeks, strict=True), 1
        ):
            if meeting.occurs_on(day, anchor_week):
                entries.append(
                    WorklogEntry(
                        day=day,
                        started=datetime.combine(
                            day, time(meeting.hour, meeting.minute), tzinfo=tz
                        ),
                        duration_min=meeting.duration_min,
                        comment=meeting.comment,
                        source=f"recurring #{index} ({meeting.comment})",
                    )
                )

    for oneoff in cfg.oneoffs:
        try:
            day = date.fromisoformat(oneoff.date)
        except ValueError as exc:
            raise ConfigurationError(f"bad one-off date {oneoff.date!r}: {exc}") from exc
        if day in excluded or not (start <= day <= end):
            continue
        entries.append(
            WorklogEntry(
                day=day,
                started=datetime.combine(day, time(oneoff.hour, oneoff.minute), tzinfo=tz),
                duration_min=oneoff.duration_min,
                comment=oneoff.comment,
                source="one-off",
            )
        )

    entries.sort(key=lambda entry: entry.started)
    return entries


def total_minutes(entries: list[WorklogEntry]) -> int:
    return sum(entry.duration_min for entry in entries)


# ---- SC-9: exporting the planned schedule -----------------------------------
def export_entries(entries: Sequence[WorklogEntry], path: Path, *, issue_key: str) -> None:
    """Write the planned entries to ``.csv``, ``.json`` or ``.ics``.

    Mirrors ``dashboard.export()``'s CSV/JSON style. Only ever called behind an
    explicit ``--export`` flag on ``preview``; the terminal table still prints
    alongside it.
    """
    suffix = path.suffix.lower()
    if suffix not in (".csv", ".json", ".ics"):
        raise ConfigurationError(f"unknown export format {path.suffix!r}; use .csv, .json or .ics")
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".csv":
        _export_csv(entries, path)
    elif suffix == ".json":
        _export_json(entries, path)
    else:
        _export_ics(entries, path, issue_key)


def _entry_rows(entries: Sequence[WorklogEntry]) -> list[dict[str, Any]]:
    return [
        {
            "date": f"{entry.started:%Y-%m-%d}",
            "day": f"{entry.started:%a}",
            "time": f"{entry.started:%H:%M}",
            "duration_min": entry.duration_min,
            "comment": entry.comment,
        }
        for entry in entries
    ]


def _export_csv(entries: Sequence[WorklogEntry], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["date", "day", "time", "duration_min", "comment"]
        )
        writer.writeheader()
        writer.writerows(_entry_rows(entries))


def _export_json(entries: Sequence[WorklogEntry], path: Path) -> None:
    path.write_text(json.dumps(_entry_rows(entries), indent=2), encoding="utf-8")


def _escape_ics_text(value: str) -> str:
    """RFC 5545 §3.3.11 TEXT escaping: backslash, comma, semicolon and newline."""
    out: list[str] = []
    for char in value:
        if char == "\\":
            out.append("\\\\")
        elif char == ";":
            out.append("\\;")
        elif char == ",":
            out.append("\\,")
        elif char == "\n":
            out.append("\\n")
        else:
            out.append(char)
    return "".join(out)


def _export_ics(entries: Sequence[WorklogEntry], path: Path, issue_key: str) -> None:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//trackspace-worklog-scheduler//EN"]
    utc = ZoneInfo("UTC")
    for entry in entries:
        start_utc = entry.started.astimezone(utc)
        end_utc = (entry.started + timedelta(minutes=entry.duration_min)).astimezone(utc)
        uid = f"trackspace-{issue_key}-{entry.started:%Y-%m-%d}-{entry.started:%H%M}@trackspace"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTART:{start_utc:%Y%m%dT%H%M%S}Z",
            f"DTEND:{end_utc:%Y%m%dT%H%M%S}Z",
            f"SUMMARY:{_escape_ics_text(entry.comment)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    # RFC 5545 mandates CRLF line endings.
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")


# ---- quick ranges ----------------------------------------------------------
def quick_range(name: str, today: date | None = None) -> tuple[date, date]:
    """The four presets the original offered, with identical arithmetic."""
    now = today or today_local()
    if name == "this-month":
        first = now.replace(day=1)
        following = (first + timedelta(days=32)).replace(day=1)
        return first, following - timedelta(days=1)
    if name == "last-month":
        first_this = now.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if name == "last-30":
        return now - timedelta(days=29), now
    if name == "this-week":
        monday = now - timedelta(days=now.weekday())
        return monday, monday + timedelta(days=6)
    raise ConfigurationError(
        f"unknown range {name!r}; use this-month, last-month, last-30 or this-week"
    )


QUICK_RANGES = ("this-month", "last-month", "last-30", "this-week")


# ---- spec parsing (the flag equivalents of the interactive editors) --------
def parse_weekdays(spec: str) -> list[int]:
    """``MON-FRI``, ``MON,WED``, ``0,2`` or ``DAILY`` → weekday indices."""
    text = spec.strip().upper()
    if text in {"DAILY", "ALL"}:
        return [0, 1, 2, 3, 4, 5, 6]
    if text in {"WEEKDAYS", "MON-FRI"}:
        return [0, 1, 2, 3, 4]

    days: set[int] = set()
    for part in text.split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "-" in chunk:
            first, _, last = chunk.partition("-")
            start, end = _weekday_index(first), _weekday_index(last)
            span = range(start, end + 1) if start <= end else [*range(start, 7), *range(0, end + 1)]
            days.update(span)
        else:
            days.add(_weekday_index(chunk))
    if not days:
        raise ConfigurationError(f"no weekday in {spec!r}")
    return sorted(days)


def _weekday_index(token: str) -> int:
    text = token.strip().upper()
    if text.isdigit():
        value = int(text)
        if 0 <= value <= 6:
            return value
        raise ConfigurationError(f"weekday index {value} is outside 0-6")
    if text[:3] in _WEEKDAY_LOOKUP:
        return _WEEKDAY_LOOKUP[text[:3]]
    raise ConfigurationError(f"unknown weekday {token!r}; use Mon..Sun or 0..6")


def parse_recurring_spec(spec: str) -> RecurringMeeting:
    """``MON-FRI@10:00+30=Daily`` → a recurring meeting.

    ``/N`` after the days makes it every N weeks, and ``~YYYY-MM-DD`` names a week
    it definitely happens in::

        TUE/2@14:00+60=Bi-weekly sync
        TUE/2~2026-07-21@14:00+60=Bi-weekly sync
    """
    match = _RECURRING_SPEC.match(spec.strip())
    if not match:
        raise ConfigurationError(
            f"invalid --recurring value {spec!r}. Expected "
            "DAYS[/EVERY_N_WEEKS][~ANCHOR]@HH:MM+MINUTES=COMMENT, "
            "e.g. MON-FRI@10:00+30=Daily or TUE/2@14:00+60=Bi-weekly sync"
        )
    hour, minute = int(match["hour"]), int(match["minute"])
    _check_clock(hour, minute, spec)
    duration = _duration_minutes(match["duration"])
    if duration <= 0:
        raise ConfigurationError(f"duration must be positive in {spec!r}")
    interval = int(match["interval"] or 1)
    if interval <= 0:
        raise ConfigurationError(f"the week interval must be positive in {spec!r}")
    anchor = match["anchor"] or ""
    if anchor:
        parse_iso_date(anchor, "anchor date")
    return RecurringMeeting(
        weekdays=parse_weekdays(match["days"]),
        hour=hour,
        minute=minute,
        duration_min=duration,
        comment=match["comment"].strip(),
        interval_weeks=interval,
        anchor=anchor,
    )


def parse_oneoff_spec(spec: str) -> OneOffMeeting:
    """``2026-04-03@13:00+30=Workshop`` → a one-off meeting."""
    match = _ONEOFF_SPEC.match(spec.strip())
    if not match:
        raise ConfigurationError(
            f"invalid --oneoff value {spec!r}. Expected YYYY-MM-DD@HH:MM+MINUTES=COMMENT, "
            "e.g. 2026-04-03@13:00+30=Workshop"
        )
    try:
        day = date.fromisoformat(match["date"])
    except ValueError as exc:
        raise ConfigurationError(f"invalid date in {spec!r}: {exc}") from exc
    hour, minute = int(match["hour"]), int(match["minute"])
    _check_clock(hour, minute, spec)
    duration = _duration_minutes(match["duration"])
    if duration <= 0:
        raise ConfigurationError(f"duration must be positive in {spec!r}")
    return OneOffMeeting(
        date=day.isoformat(),
        hour=hour,
        minute=minute,
        duration_min=duration,
        comment=match["comment"].strip(),
    )


def _check_clock(hour: int, minute: int, spec: str) -> None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigurationError(f"invalid time in {spec!r}; hours 00-23, minutes 00-59")


def parse_iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ConfigurationError(f"invalid {label} {value!r}: use YYYY-MM-DD") from exc
