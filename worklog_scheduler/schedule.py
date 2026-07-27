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

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from trackspace.errors import ConfigurationError

from .clock import today_local
from .config import WEEKDAY_NAMES, OneOffMeeting, RecurringMeeting, ScheduleConfig

_WEEKDAY_LOOKUP = {name.upper(): index for index, name in enumerate(WEEKDAY_NAMES)}

_RECURRING_SPEC = re.compile(
    r"^(?P<days>[A-Za-z0-9,\-]+)@(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"\+(?P<duration>\d+)=(?P<comment>.+)$"
)
_ONEOFF_SPEC = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})@(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"\+(?P<duration>\d+)=(?P<comment>.+)$"
)


@dataclass(frozen=True)
class WorklogEntry:
    """One worklog that would be posted."""

    day: date
    started: datetime
    duration_min: int
    comment: str


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

    for day in daterange(start, end):
        if day in excluded:
            continue
        for meeting in cfg.recurring:
            if day.weekday() in meeting.weekdays:
                entries.append(
                    WorklogEntry(
                        day=day,
                        started=datetime.combine(
                            day, time(meeting.hour, meeting.minute), tzinfo=tz
                        ),
                        duration_min=meeting.duration_min,
                        comment=meeting.comment,
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
            )
        )

    entries.sort(key=lambda entry: entry.started)
    return entries


def total_minutes(entries: list[WorklogEntry]) -> int:
    return sum(entry.duration_min for entry in entries)


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
    """``MON-FRI@10:00+30=Daily`` → a recurring meeting."""
    match = _RECURRING_SPEC.match(spec.strip())
    if not match:
        raise ConfigurationError(
            f"invalid --recurring value {spec!r}. Expected DAYS@HH:MM+MINUTES=COMMENT, "
            "e.g. MON-FRI@10:00+30=Daily"
        )
    hour, minute = int(match["hour"]), int(match["minute"])
    _check_clock(hour, minute, spec)
    duration = int(match["duration"])
    if duration <= 0:
        raise ConfigurationError(f"duration must be positive in {spec!r}")
    return RecurringMeeting(
        weekdays=parse_weekdays(match["days"]),
        hour=hour,
        minute=minute,
        duration_min=duration,
        comment=match["comment"].strip(),
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
    duration = int(match["duration"])
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
