"""Dense, aligned, bordered tables.

Long values are truncated with a marker rather than wrapped: a worklog table with
one row per entry is only readable if a row stays one line.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.table import Table
from rich.text import Text

from .theme import table_box, unicode_ok

Justify = Literal["left", "right", "center"]


@dataclass(frozen=True)
class Column:
    """One column: header, width budget and alignment."""

    header: str
    width: int | None = None
    justify: Justify = "left"
    style: str = ""
    #: Columns like a comment get the leftover width; ids and numbers do not.
    flex: bool = False


def truncate(value: str, width: int | None, *, console: Console | None = None) -> str:
    """Cut to ``width`` characters, marking the cut. ``None`` means never cut."""
    if width is None or width <= 0 or len(value) <= width:
        return value
    marker = "…" if unicode_ok(console.file if console else None) else "..."
    keep = max(0, width - len(marker))
    return value[:keep] + marker


def render_table(
    console: Console,
    columns: Sequence[Column],
    rows: Sequence[Sequence[str]],
    *,
    title: str | None = None,
    caption: str | None = None,
) -> None:
    """Print a bordered table, truncating each cell to its column width."""
    table = Table(
        box=table_box(console),
        title=Text(title, style="heading") if title else None,
        title_justify="left",
        caption=Text(caption, style="muted") if caption else None,
        caption_justify="left",
        pad_edge=False,
        padding=(0, 1),
        expand=False,
    )
    for column in columns:
        table.add_column(
            column.header,
            justify=column.justify,
            style=column.style or None,
            no_wrap=True,
            overflow="ignore",
            ratio=1 if column.flex else None,
        )
    for row in rows:
        table.add_row(
            *(
                truncate(str(cell), column.width, console=console)
                for cell, column in zip(row, columns, strict=False)
            )
        )
    console.print(table)


def empty_notice(console: Console, message: str) -> None:
    """What to print where a table would have gone, when there is nothing."""
    console.print(Text(f"  {message}", style="muted"))
