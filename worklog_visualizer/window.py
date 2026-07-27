"""Choosing the time window.

Three mutually exclusive spellings, unchanged from the original tool: a rolling
``--ago`` offset, an absolute ``--date`` range, or an absolute ``--datetime``
range. Absolute values are interpreted in the machine's local zone.
"""

from __future__ import annotations

import argparse
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from dateutil.relativedelta import relativedelta

#: Default rolling window when no time-window flag is given.
DEFAULT_AGO = "30d"

# Absolute --date / --datetime values are interpreted in this timezone.
# Picks up whatever the OS thinks "local" is, which is what the user means
# when they type a wall-clock date or datetime.
LOCAL_TZ = datetime.now(UTC).astimezone().tzinfo

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


def window_from_ago(spec: str) -> tuple[datetime, datetime, str]:
    """A rolling window ending now."""
    offset, human = parse_ago(spec)
    end_dt = datetime.now(LOCAL_TZ)
    return end_dt - offset, end_dt, f"last {human}"


def window_from_dates(start: date, end: date) -> tuple[datetime, datetime, str]:
    """A whole-day window covering both endpoints."""
    return (
        datetime.combine(start, time(0, 0, 0), tzinfo=LOCAL_TZ),
        datetime.combine(end, time(23, 59, 59), tzinfo=LOCAL_TZ),
        "selected range",
    )


def resolve_window(args: argparse.Namespace) -> tuple[datetime, datetime, str]:
    """Turn the parsed CLI flags into (start_dt, end_dt, window_label).

    Exactly one of --ago / --date / --datetime is honoured (argparse enforces
    mutual exclusion); when none is given, the default --ago window is used.
    """
    if args.datetime_range:
        sn, en = parse_datetime_range(args.datetime_range)
        return sn.replace(tzinfo=LOCAL_TZ), en.replace(tzinfo=LOCAL_TZ), "selected range"

    if args.date_range:
        sd, ed = parse_date_range(args.date_range)
        return window_from_dates(sd, ed)

    # Default / explicit --ago path.
    return window_from_ago(args.ago or DEFAULT_AGO)
