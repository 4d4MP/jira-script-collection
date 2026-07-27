"""Shared CLI look and feel. Import from here so tools cannot drift apart."""

from .charts import bar_chart, legend, series_palette, sparkline, stacked_bar_chart
from .chrome import (
    EXIT_CANCELLED,
    LiveStatus,
    RunSummary,
    cancellable,
    console_pair,
    final,
    header,
    install_sigint_handler,
    is_interactive,
    key_value_panel,
    notice,
)
from .tables import Column, empty_notice, render_table, truncate
from .theme import (
    SERIES_COLORS,
    Kind,
    color_disabled,
    glyph,
    make_console,
    status_text,
    table_box,
    unicode_ok,
)

__all__ = [
    "EXIT_CANCELLED",
    "SERIES_COLORS",
    "Column",
    "Kind",
    "LiveStatus",
    "RunSummary",
    "bar_chart",
    "cancellable",
    "color_disabled",
    "console_pair",
    "empty_notice",
    "final",
    "glyph",
    "header",
    "install_sigint_handler",
    "is_interactive",
    "key_value_panel",
    "legend",
    "make_console",
    "notice",
    "render_table",
    "series_palette",
    "sparkline",
    "stacked_bar_chart",
    "status_text",
    "table_box",
    "truncate",
    "unicode_ok",
]
