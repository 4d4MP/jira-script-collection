"""Today, in the machine's local zone.

Its own module so both the config defaults and the schedule maths can use it
without importing each other.
"""

from __future__ import annotations

from datetime import UTC, date, datetime


def today_local() -> date:
    """Today's calendar date locally.

    Spelled via an aware UTC ``now()`` so no naive datetime is ever constructed;
    the result is identical to ``date.today()``.
    """
    return datetime.now(UTC).astimezone().date()
