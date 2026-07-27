"""Trackspace worklog visualiser.

Renders what you (or a colleague) logged over a time window as a multi-panel PNG.
Renamed from the original ``work.py`` / ``visualize_jira_worklogs.py``.
"""

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    from .visualize_logged_worklogs import main as _main

    return _main(argv)
