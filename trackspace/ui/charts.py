"""Charts that live in the terminal.

Distributions, timelines and rankings render as Unicode bars by default. Nothing
here writes a file — an image is only ever produced when a tool is given an
explicit export flag, and even then the terminal rendering still prints.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from rich.console import Console, RenderableType
from rich.table import Table
from rich.text import Text

from .theme import SERIES_COLORS, bar_glyphs, unicode_ok

_SPARK_UNICODE = "▁▂▃▄▅▆▇█"
_SPARK_ASCII = "_.-~=+*#"

DEFAULT_BAR_WIDTH = 36
#: Room kept for the value column and the two column gaps.
_VALUE_GUTTER = 10


def fit_widths(console: Console, label_width: int, bar_width: int) -> tuple[int, int]:
    """Shrink the label, then the bar, until a row fits the terminal.

    Without this a long ticket summary pushes the value column off the right edge,
    which is exactly the unreadable wrapping the tables rule forbids.
    """
    available = max(30, console.width) - _VALUE_GUTTER
    if label_width + bar_width <= available:
        return label_width, bar_width
    label_width = max(12, min(label_width, available - bar_width))
    bar_width = max(8, min(bar_width, available - label_width))
    return label_width, bar_width


def series_palette(names: Iterable[str]) -> dict[str, str]:
    """Stable colour per series, so a ticket keeps its colour across panels."""
    return {name: SERIES_COLORS[i % len(SERIES_COLORS)] for i, name in enumerate(names)}


def sparkline(values: Sequence[float], *, console: Console | None = None) -> str:
    """One-line shape of a series. Empty or all-zero input gives a flat line."""
    ramp = _SPARK_UNICODE if unicode_ok(console.file if console else None) else _SPARK_ASCII
    if not values:
        return ""
    peak = max(values)
    if peak <= 0:
        return ramp[0] * len(values)
    step = len(ramp) - 1
    return "".join(ramp[round(value / peak * step)] for value in values)


def bar_chart(
    rows: Sequence[tuple[str, float]],
    *,
    console: Console,
    width: int = DEFAULT_BAR_WIDTH,
    value_format: str = "{:.1f}h",
    label_width: int = 46,
    color: str = "info",
) -> RenderableType:
    """Horizontal bars, biggest-first ordering left to the caller."""
    filled, _ = bar_glyphs(console.file)
    label_width, width = fit_widths(console, label_width, width)
    peak = max((value for _, value in rows), default=0.0)
    grid = Table.grid(padding=(0, 1))
    grid.add_column(justify="left", no_wrap=True, width=label_width)
    grid.add_column(justify="left", no_wrap=True, width=width)
    grid.add_column(justify="right", no_wrap=True)

    for label, value in rows:
        cells = 0 if peak <= 0 or value <= 0 else max(1, round(value / peak * width))
        bar = Text(filled * cells, style=color)
        text_label = label if len(label) <= label_width else label[: label_width - 1] + "…"
        grid.add_row(Text(text_label), bar, Text(value_format.format(value), style="value"))
    return grid


def stacked_bar_chart(
    rows: Sequence[tuple[str, Mapping[str, float]]],
    *,
    console: Console,
    palette: Mapping[str, str],
    width: int = DEFAULT_BAR_WIDTH,
    value_format: str = "{:.1f}",
    label_width: int = 14,
    dim_rows: Sequence[str] = (),
) -> RenderableType:
    """One row per bucket (a day, usually), segmented by series.

    Bars share a single scale — the largest bucket total fills ``width`` — so the
    rows are comparable by eye. Any non-zero segment gets at least one cell, so a
    six-minute entry is visible rather than rounded away.
    """
    filled, _ = bar_glyphs(console.file)
    label_width, width = fit_widths(console, label_width, width)
    totals = {label: sum(values.values()) for label, values in rows}
    peak = max(totals.values(), default=0.0)
    dim = set(dim_rows)

    grid = Table.grid(padding=(0, 1))
    grid.add_column(justify="left", no_wrap=True, width=label_width)
    grid.add_column(justify="left", no_wrap=True, width=width)
    grid.add_column(justify="right", no_wrap=True)

    for label, values in rows:
        bar = Text()
        if peak > 0:
            for series, value in values.items():
                if value <= 0:
                    continue
                cells = max(1, round(value / peak * width))
                bar.append(filled * cells, style=palette.get(series, "info"))
        total = totals[label]
        grid.add_row(
            Text(label, style="muted" if label in dim else ""),
            bar,
            Text(value_format.format(total) if total > 0 else "", style="value"),
        )
    return grid


def legend(
    palette: Mapping[str, str],
    *,
    console: Console,
    values: Mapping[str, float] | None = None,
    value_format: str = "{:.1f}h",
    columns: int = 3,
) -> RenderableType:
    """Colour key for a stacked chart, optionally with each series' total."""
    filled, _ = bar_glyphs(console.file)
    grid = Table.grid(padding=(0, 3))
    for _ in range(columns):
        grid.add_column(justify="left", no_wrap=True)

    cells: list[Text] = []
    for series, color in palette.items():
        cell = Text()
        cell.append(f"{filled}{filled} ", style=color)
        cell.append(series)
        if values is not None and series in values:
            cell.append(f"  {value_format.format(values[series])}", style="muted")
        cells.append(cell)

    for start in range(0, len(cells), columns):
        chunk = cells[start : start + columns]
        chunk += [Text("")] * (columns - len(chunk))
        grid.add_row(*chunk)
    return grid
