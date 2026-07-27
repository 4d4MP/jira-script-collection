"""The frame around every tool: boxed header, live status, clean cancellation,
outcome-first closing message.

Import these rather than writing your own — the point is that two tools launched
side by side look like the same product.
"""

from __future__ import annotations

import contextlib
import signal
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import FrameType
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .theme import Kind, glyph, make_console, status_text, table_box

#: Exit code for a run the user interrupted, by convention 128 + SIGINT.
EXIT_CANCELLED = 130


def header(
    console: Console,
    *,
    tool: str,
    instance: str,
    auth_present: bool,
    auth_label: str,
    rows: Sequence[tuple[str, str]] = (),
) -> None:
    """Boxed header: what is running, where it points, whether auth is available.

    ``auth_label`` says *present* or *missing* and where it came from. The token
    itself is never passed in, let alone rendered.
    """
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="muted", justify="right", no_wrap=True)
    grid.add_column(style="value", overflow="fold")

    grid.add_row("instance", instance)
    auth_kind: Kind = "success" if auth_present else "error"
    grid.add_row("auth", status_text(auth_kind, auth_label, console=console))
    for label, value in rows:
        grid.add_row(label, value)

    console.print(
        Panel(
            grid,
            title=Text(tool, style="heading"),
            title_align="left",
            box=table_box(console),
            border_style="info",
            padding=(0, 1),
        )
    )


@dataclass
class RunSummary:
    """Counters and notes accumulated during a run.

    Printed on success, and — the reason it exists — printed on Ctrl+C too, so an
    interrupted run still says what it managed to finish.
    """

    entries: list[tuple[str, str]] = field(default_factory=list)

    def record(self, label: str, value: Any) -> None:
        self.entries.append((label, str(value)))

    def replace(self, label: str, value: Any) -> None:
        """Set a counter, overwriting any earlier value for the same label."""
        for index, (existing, _) in enumerate(self.entries):
            if existing == label:
                self.entries[index] = (label, str(value))
                return
        self.record(label, value)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def render(self) -> RenderableType:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style="muted", justify="right", no_wrap=True)
        grid.add_column(style="value")
        for label, value in self.entries:
            grid.add_row(label, value)
        return grid


class LiveStatus:
    """A status line that updates in place, naming the operation and its counts.

    On a terminal this is a spinner plus live counters. Without a terminal — a CI
    log, a pipe, ``TERM=dumb`` — it degrades to one plain line per distinct
    message, so logs stay readable instead of filling with control codes.
    """

    def __init__(self, console: Console, message: str = "Working") -> None:
        self.console = console
        self._message = message
        self._counters: dict[str, int] = {}
        self._live: Live | None = None
        self._last_plain = ""
        self._interactive = console.is_terminal and not console.is_dumb_terminal
        spinner_name = "dots" if self._interactive else "line"
        self._spinner = Spinner(spinner_name, text="", style="pending")

    # -- rendering --
    def _render(self) -> RenderableType:
        text = Text()
        text.append(self._message)
        if self._counters:
            counts = "  ".join(f"{name} {value}" for name, value in self._counters.items())
            text.append("   ")
            text.append(counts, style="muted")
        self._spinner.update(text=text)
        return self._spinner

    def _plain_line(self) -> str:
        counts = "  ".join(f"{name} {value}" for name, value in self._counters.items())
        return f"{self._message}   {counts}".rstrip()

    # -- lifecycle --
    def __enter__(self) -> LiveStatus:
        if self._interactive:
            self._live = Live(
                self._render(),
                console=self.console,
                refresh_per_second=12,
                transient=True,
            )
            self._live.start()
        else:
            self._emit_plain()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    # -- updates --
    def update(self, message: str | None = None, **counters: int) -> None:
        if message is not None:
            self._message = message
        self._counters.update(counters)
        if self._live is not None:
            self._live.update(self._render())
        else:
            self._emit_plain()

    def bump(self, **counters: int) -> None:
        for name, delta in counters.items():
            self._counters[name] = self._counters.get(name, 0) + delta
        if self._live is not None:
            self._live.update(self._render())

    def _emit_plain(self) -> None:
        line = self._plain_line()
        if line != self._last_plain:
            self.console.print(status_text("pending", line, console=self.console))
            self._last_plain = line

    def log(self, kind: Kind, message: str) -> None:
        """Print a permanent line without disturbing the live status."""
        if self._live is not None:
            self._live.console.print(status_text(kind, message, console=self.console))
        else:
            self.console.print(status_text(kind, message, console=self.console))


@contextmanager
def cancellable(console: Console, summary: RunSummary) -> Iterator[RunSummary]:
    """Turn Ctrl+C anywhere inside into a clean exit with a short summary.

    In-flight work is abandoned at the next Python bytecode boundary — there is no
    partial-write risk here because the only mutating call is a single worklog
    POST, which either completed or never left.
    """
    try:
        yield summary
    except KeyboardInterrupt:
        console.print()
        console.print(
            status_text("warning", "Cancelled — stopped in-flight work.", console=console)
        )
        if summary:
            console.print(summary.render())
        raise SystemExit(EXIT_CANCELLED) from None


def final(
    console: Console,
    kind: Kind,
    outcome: str,
    details: Sequence[str] = (),
    summary: RunSummary | None = None,
) -> None:
    """Outcome first, detail after."""
    console.print()
    console.print(status_text(kind, outcome, console=console))
    for line in details:
        console.print(Text(f"  {line}", style="muted"))
    if summary:
        console.print(summary.render())


def notice(console: Console, kind: Kind, message: str) -> None:
    console.print(status_text(kind, message, console=console))


def key_value_panel(title: str, rows: Sequence[tuple[str, str]], console: Console) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="muted", justify="right", no_wrap=True)
    grid.add_column(style="value")
    for label, value in rows:
        grid.add_row(label, value)
    return Panel(
        grid,
        title=Text(title, style="heading"),
        title_align="left",
        box=table_box(console),
        border_style="muted",
        padding=(0, 1),
    )


def grouped(*renderables: RenderableType) -> RenderableType:
    return Group(*renderables)


def install_sigint_handler() -> None:
    """Make SIGINT raise KeyboardInterrupt even from inside a rich Live region.

    Rich installs its own handler while a Live is running; restoring the default
    keeps Ctrl+C responsive at every point of a long fetch.
    """

    def _handler(_signum: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    # Not on the main thread (or no signal support): Ctrl+C still reaches the
    # cancellation handler through the default behaviour.
    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGINT, _handler)


def console_pair() -> tuple[Console, Console]:
    """``(out, err)`` consoles. Progress and diagnostics go to stderr so that
    piping a tool's structured output stays clean."""
    return make_console(), make_console(stderr=True)


def is_interactive() -> bool:
    """True when both ends of the conversation are a real terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()


__all__ = [
    "EXIT_CANCELLED",
    "Kind",
    "LiveStatus",
    "RunSummary",
    "cancellable",
    "console_pair",
    "final",
    "glyph",
    "grouped",
    "header",
    "install_sigint_handler",
    "is_interactive",
    "key_value_panel",
    "make_console",
    "notice",
    "status_text",
]
