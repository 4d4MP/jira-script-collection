"""Trackspace issue companion.

Companion actions on a single Trackspace issue that the two worklog tools never
needed: transitions (list only), comments, attachments, issue/remote links, and
the changelog. See ``kb/proposals/capability-audit.md`` NT-2..NT-6.
"""

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    from .cli import main as _main

    return _main(argv)
