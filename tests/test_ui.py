"""The shared CLI components: degradation, truncation, charts, cancellation."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from trackspace.ui import charts, chrome, tables, theme


def recording_console(width: int = 120) -> Console:
    """The real themed console, recording instead of printing."""
    console = theme.make_console(record=True)
    console.width = width
    return console


def test_no_color_disables_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert theme.color_disabled()
    assert theme.make_console().color_system is None


def test_dumb_terminal_disables_colour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert theme.make_console().color_system is None


def test_glyphs_fall_back_to_ascii_on_a_limited_encoding() -> None:
    ascii_stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    assert not theme.unicode_ok(ascii_stream)
    utf8_stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    assert theme.unicode_ok(utf8_stream)
    ascii_console = Console(file=ascii_stream, color_system=None)
    assert theme.glyph("success", console=ascii_console) == "+"
    assert theme.bar_glyphs(ascii_stream) == ("#", ".")


def test_state_glyphs_are_fixed() -> None:
    console = recording_console()
    assert theme.glyph("success", console=console) == "✔"
    assert theme.glyph("warning", console=console) == "▲"
    assert theme.glyph("error", console=console) == "✖"
    assert theme.glyph("info", console=console) == "•"


def test_header_shows_auth_state_without_the_token() -> None:
    console = recording_console()
    chrome.header(
        console,
        tool="Trackspace worklog scheduler",
        instance="https://trackspace.lhsystems.com",
        auth_present=True,
        auth_label="present (from TRACKSPACE_PAT)",
        rows=[("issue", "CLOPSSEC-41456")],
    )
    output = console.export_text()
    assert "Trackspace worklog scheduler" in output
    assert "https://trackspace.lhsystems.com" in output
    assert "present (from TRACKSPACE_PAT)" in output
    assert "CLOPSSEC-41456" in output


def test_truncation_marks_the_cut() -> None:
    console = recording_console()
    assert tables.truncate("short", 10, console=console) == "short"
    assert tables.truncate("x" * 20, 10, console=console) == "x" * 9 + "…"
    assert tables.truncate("x" * 20, None, console=console) == "x" * 20


def test_table_truncates_rather_than_wraps() -> None:
    console = recording_console()
    tables.render_table(
        console,
        [tables.Column("Key", width=8), tables.Column("Comment", width=20)],
        [["CLOPSSEC-41456", "a very long comment that must not wrap onto a second line"]],
        title="Rows",
    )
    lines = [line for line in console.export_text().splitlines() if "CLOPSS" in line]
    assert len(lines) == 1
    assert "…" in lines[0]


def test_bar_chart_scales_to_the_largest_value() -> None:
    console = recording_console()
    console.print(
        charts.bar_chart([("big", 10.0), ("small", 1.0), ("none", 0.0)], console=console, width=10)
    )
    output = console.export_text()
    assert "██████████" in output
    assert "10.0h" in output


def test_stacked_chart_keeps_tiny_slices_visible() -> None:
    console = recording_console()
    console.print(
        charts.stacked_bar_chart(
            [("Mon", {"A": 8.0}), ("Tue", {"A": 0.1, "B": 0.1})],
            console=console,
            palette=charts.series_palette(["A", "B"]),
            width=20,
        )
    )
    lines = {line.split()[0]: line for line in console.export_text().splitlines() if line.strip()}
    assert "█" in lines["Tue"]


def test_sparkline_handles_empty_and_flat_series() -> None:
    assert charts.sparkline([]) == ""
    assert charts.sparkline([0, 0, 0]) == "▁▁▁"
    assert charts.sparkline([1, 5, 10])[-1] == "█"


def test_legend_lists_every_series() -> None:
    console = recording_console()
    palette = charts.series_palette(["CLOPSSEC-1", "CLOPSSEC-2"])
    console.print(charts.legend(palette, console=console, values={"CLOPSSEC-1": 2.0}))
    output = console.export_text()
    assert "CLOPSSEC-1" in output
    assert "2.0h" in output
    assert "CLOPSSEC-2" in output


def test_live_status_degrades_to_plain_lines_off_terminal() -> None:
    console = recording_console()
    with chrome.LiveStatus(console, "Searching issues") as status:
        status.update("Fetching worklogs [1/2]", issues=2)
        status.log("warning", "failed CLOPSSEC-1")
    output = console.export_text()
    assert "Searching issues" in output
    assert "Fetching worklogs [1/2]" in output
    assert "failed CLOPSSEC-1" in output


def test_run_summary_replaces_counters() -> None:
    summary = chrome.RunSummary()
    summary.replace("posted", 1)
    summary.replace("posted", 4)
    assert summary.entries == [("posted", "4")]


def test_ctrl_c_exits_cleanly_with_a_summary() -> None:
    console = recording_console()
    summary = chrome.RunSummary()
    summary.record("posted", 3)

    with pytest.raises(SystemExit) as caught, chrome.cancellable(console, summary):
        raise KeyboardInterrupt

    assert caught.value.code == chrome.EXIT_CANCELLED
    output = console.export_text()
    assert "Cancelled" in output
    assert "posted" in output


def test_final_leads_with_the_outcome() -> None:
    console = recording_console()
    chrome.final(console, "success", "Posted 7/7 worklogs to CLOPSSEC-41456", details=["7 entries"])
    lines = [line for line in console.export_text().splitlines() if line.strip()]
    assert lines[0].endswith("Posted 7/7 worklogs to CLOPSSEC-41456")
    assert "7 entries" in lines[1]


def test_every_style_and_series_colour_is_valid() -> None:
    from rich.style import Style

    for name, definition in theme.STYLES.items():
        assert Style.parse(definition), name
    for colour in theme.SERIES_COLORS:
        assert Style.parse(colour), colour


def test_palette_avoids_colours_that_vanish_on_a_light_terminal() -> None:
    """The washed-out ANSI basics and bare `white`/`dim` are what made the first
    palette unreadable on a white background; keep them out."""
    banned = {"white", "yellow", "cyan", "bright_yellow", "bright_cyan", "bright_white", "dim"}
    for name, definition in theme.STYLES.items():
        tokens = set(definition.split())
        assert not (tokens & banned), f"{name} uses {tokens & banned}"
    assert not set(theme.SERIES_COLORS) & banned


def test_series_colours_are_distinct() -> None:
    assert len(set(theme.SERIES_COLORS)) == len(theme.SERIES_COLORS)


def test_flex_width_keeps_a_row_inside_the_terminal() -> None:
    console = recording_console(width=80)
    width = tables.flex_width(console, [18, 7, 7, 10, 10])
    assert width >= 20
    assert 18 + 7 + 7 + 10 + 10 + width <= console.width
