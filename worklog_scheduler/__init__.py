"""Trackspace worklog scheduler.

Plans recurring and one-off meeting time, previews it, posts it, and shows what
has been logged. Renamed from the original ``work_log.py``.
"""

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    from .schedule_and_post_worklogs import main as _main

    return _main(argv)
