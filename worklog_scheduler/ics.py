"""Minimal, hand-rolled reading of ``.ics`` (RFC 5545) files.

Just enough to support ``--exclude-file`` (SC-8) and ``--import-ics`` (SC-10):
``VEVENT`` components reduced to a start instant, a duration and a summary.
Recurrence rules, alarms, timezone *definitions* (``VTIMEZONE``) and every other
component are ignored — a naive or ``TZID``-tagged ``DTSTART``/``DTEND`` is read
as being in the schedule's own configured timezone, and a ``Z``-suffixed one is
read as UTC and converted into it. No third-party dependency; stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trackspace.errors import ConfigurationError

_DTVALUE = re.compile(r"^(?P<date>\d{8})(?:T(?P<time>\d{6}))?(?P<utc>Z)?$")
_DURATION = re.compile(
    r"^P(?:(?P<weeks>\d+)W)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


@dataclass(frozen=True)
class IcsEvent:
    """One ``VEVENT``, reduced to what the scheduler needs."""

    dtstart: datetime  # tz-aware, already in the schedule's timezone
    duration_min: int
    summary: str
    all_day: bool


def unfold(text: str) -> list[str]:
    """Undo RFC 5545 line folding.

    A line that starts with a space or a tab is a continuation of the previous
    line (with that one leading character dropped); both CRLF and bare-LF input
    are accepted.
    """
    raw_lines = text.replace("\r\n", "\n").split("\n")
    lines: list[str] = []
    for raw in raw_lines:
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw.strip("﻿") != "":
            lines.append(raw.strip("﻿"))
    return lines


def _unescape(value: str) -> str:
    """``\\,`` ``\\;`` ``\\\\`` and ``\\n``/``\\N`` per RFC 5545 §3.3.11."""
    out: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in ",;\\":
                out.append(nxt)
                i += 2
                continue
            if nxt in "nN":
                out.append("\n")
                i += 2
                continue
        out.append(char)
        i += 1
    return "".join(out)


def _split_property(line: str) -> tuple[str, dict[str, str], str]:
    """``NAME;P1=V1;P2=V2:VALUE`` -> ``(NAME, {P1: V1, ...}, VALUE)``."""
    if ":" not in line:
        raise ConfigurationError(f"malformed .ics line: {line!r}")
    head, _, value = line.partition(":")
    parts = head.split(";")
    name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for part in parts[1:]:
        key, _, val = part.partition("=")
        if key:
            params[key.strip().upper()] = val.strip()
    return name, params, value


def _parse_datetime(value: str, params: dict[str, str], tz: ZoneInfo) -> tuple[datetime, bool]:
    match = _DTVALUE.match(value.strip())
    if not match:
        raise ConfigurationError(f"unreadable date/time in .ics: {value!r}")
    year, month, day = int(match["date"][0:4]), int(match["date"][4:6]), int(match["date"][6:8])
    if params.get("VALUE") == "DATE" or not match["time"]:
        return datetime(year, month, day, tzinfo=tz), True
    hour, minute, second = (
        int(match["time"][0:2]),
        int(match["time"][2:4]),
        int(match["time"][4:6]),
    )
    if match["utc"]:
        as_utc = datetime(year, month, day, hour, minute, second, tzinfo=ZoneInfo("UTC"))
        return as_utc.astimezone(tz), False
    # Naive, or TZID-tagged (the TZID name itself is not resolved — treated as
    # the schedule's own timezone, per SC-10's spec).
    return datetime(year, month, day, hour, minute, second, tzinfo=tz), False


def _parse_duration_minutes(value: str) -> int:
    match = _DURATION.match(value.strip())
    if not match or not match.group(0) or match.group(0) == "P":
        return 30
    weeks = int(match["weeks"] or 0)
    hours = int(match["hours"] or 0)
    minutes = int(match["minutes"] or 0)
    seconds = int(match["seconds"] or 0)
    total = weeks * 7 * 24 * 60 + hours * 60 + minutes + seconds // 60
    return total or 30


def parse_ics(text: str, tz: ZoneInfo) -> list[IcsEvent]:
    """Every ``VEVENT`` in ``text``, in document order."""
    events: list[IcsEvent] = []
    props: dict[str, tuple[dict[str, str], str]] = {}
    in_event = False
    for line in unfold(text):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            in_event = True
            props = {}
            continue
        if upper == "END:VEVENT":
            in_event = False
            events.append(_build_event(props, tz))
            continue
        if not in_event:
            continue
        name, params, value = _split_property(line)
        props[name] = (params, value)
    return events


def _build_event(props: dict[str, tuple[dict[str, str], str]], tz: ZoneInfo) -> IcsEvent:
    if "DTSTART" not in props:
        raise ConfigurationError("a VEVENT in the .ics file has no DTSTART")
    dtstart_params, dtstart_value = props["DTSTART"]
    dtstart, all_day = _parse_datetime(dtstart_value, dtstart_params, tz)

    duration_min = 30
    if "DTEND" in props:
        dtend_params, dtend_value = props["DTEND"]
        dtend, _ = _parse_datetime(dtend_value, dtend_params, tz)
        duration_min = max(0, int((dtend - dtstart).total_seconds() // 60))
    elif "DURATION" in props:
        duration_min = _parse_duration_minutes(props["DURATION"][1])

    summary = _unescape(props["SUMMARY"][1]) if "SUMMARY" in props else ""
    return IcsEvent(dtstart=dtstart, duration_min=duration_min, summary=summary, all_day=all_day)


def read_ics_file(path: Path, tz: ZoneInfo) -> list[IcsEvent]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    return parse_ics(text, tz)


def read_exclude_dates(path: Path, tz: ZoneInfo) -> list[date]:
    """SC-8: dates to exclude, from a plain-text list or an ``.ics`` calendar.

    The plain-text form is one ``YYYY-MM-DD`` per line; blank lines and lines
    starting with ``#`` are ignored. The ``.ics`` form reuses SC-10's event
    parser and takes each ``VEVENT``'s ``DTSTART`` date, all-day or not.
    """
    if path.suffix.lower() == ".ics":
        events = read_ics_file(path, tz)
        return sorted({event.dtstart.date() for event in events})

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc

    dates: list[date] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            dates.append(date.fromisoformat(line))
        except ValueError as exc:
            raise ConfigurationError(f"bad date on line {lineno} of {path}: {line!r}") from exc
    return dates
