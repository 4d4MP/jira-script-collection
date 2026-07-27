"""Colour and glyph conventions, defined once so no tool can drift.

Four states, fixed everywhere: success, warning, error, info. Plus two support
styles (``pending``, ``muted``) for in-flight work and secondary detail.

Degradation rules:

* ``NO_COLOR`` set, or a dumb/non-terminal stream → no colour at all.
* Output encoding cannot represent the glyphs → ASCII markers instead.
"""

from __future__ import annotations

import os
import sys
from typing import IO, Literal

from rich import box
from rich.box import Box
from rich.console import Console
from rich.text import Text
from rich.theme import Theme

Kind = Literal["success", "warning", "error", "info", "pending", "muted"]

# Every colour here is a mid-luminance 256-colour value, chosen to stay legible
# on a white terminal as well as a black one. The ANSI basics are avoided on
# purpose: `yellow` and `cyan` wash out on light backgrounds, and `white` on a
# light background is invisible — which is why `value` carries no colour at all
# and simply inherits the terminal's foreground.
STYLES: dict[str, str] = {
    "success": "bold green4",
    "warning": "bold dark_orange3",
    "error": "bold red3",
    "info": "bold deep_sky_blue3",
    "pending": "deep_sky_blue3",
    "muted": "grey50",
    "heading": "bold",
    "value": "bold",
    "accent": "medium_purple3",
}

_UNICODE_GLYPHS: dict[str, str] = {
    "success": "✔",
    "warning": "▲",
    "error": "✖",
    "info": "•",
    "pending": "…",
    "muted": " ",
}

_ASCII_GLYPHS: dict[str, str] = {
    "success": "+",
    "warning": "!",
    "error": "x",
    "info": "-",
    "pending": ".",
    "muted": " ",
}

#: Palette for chart series: distinct from each other, and all readable against a
#: white or a black background. Ordered so neighbouring series contrast.
SERIES_COLORS: tuple[str, ...] = (
    "deep_sky_blue3",
    "dark_orange3",
    "green4",
    "medium_purple3",
    "dark_cyan",
    "deep_pink3",
    "gold3",
    "steel_blue",
    "indian_red",
    "dark_olive_green3",
)

_RICH_THEME = Theme(STYLES)


def color_disabled() -> bool:
    """``NO_COLOR`` is honoured whatever its value, per the no-color.org spec."""
    return "NO_COLOR" in os.environ


def _dumb_terminal() -> bool:
    return os.environ.get("TERM", "").lower() in {"dumb", "unknown"}


def unicode_ok(stream: IO[str] | None = None) -> bool:
    """Can the output stream actually encode the glyphs and block characters?"""
    encoding = getattr(stream or sys.stdout, "encoding", None) or "ascii"
    try:
        "✔█░…".encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def make_console(*, stderr: bool = False, record: bool = False) -> Console:
    """Build a console that already knows about ``NO_COLOR`` and dumb terminals."""
    plain = color_disabled() or _dumb_terminal()
    return Console(
        theme=_RICH_THEME,
        stderr=stderr,
        record=record,
        color_system=None if plain else "auto",
        highlight=False,
        soft_wrap=False,
        emoji=False,
    )


def glyph(kind: Kind, *, console: Console | None = None) -> str:
    stream = console.file if console is not None else None
    table = _UNICODE_GLYPHS if unicode_ok(stream) else _ASCII_GLYPHS
    return table[kind]


def status_text(kind: Kind, message: str, *, console: Console | None = None) -> Text:
    """``✔ message`` with the glyph in the state colour and the text plain."""
    text = Text()
    text.append(f"{glyph(kind, console=console)} ", style=kind)
    text.append(message)
    return text


def table_box(console: Console | None = None) -> Box:
    """Bordered normally, ASCII-bordered where Unicode would break."""
    stream = console.file if console is not None else None
    return box.SIMPLE_HEAD if not unicode_ok(stream) else box.ROUNDED


def bar_glyphs(stream: IO[str] | None = None) -> tuple[str, str]:
    """``(filled, empty)`` characters for bar charts, given an output stream."""
    return ("█", "░") if unicode_ok(stream) else ("#", ".")
