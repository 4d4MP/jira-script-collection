#!/usr/bin/env python3
"""Trackspace worklog visualiser.

Shows the work you (or a colleague) logged across a time window: hours stacked by
ticket over the timeline, the top tickets grouped by title with IP addresses
collapsed, a per-ticket table, and the summary figures.

Interactive by default::

    python -m worklog_visualizer

Fully scriptable too::

    python -m worklog_visualizer report --ago 7d
    python -m worklog_visualizer report --date 2026_04_01-2026_04_30 --user colleague.name
    python -m worklog_visualizer report --ago 1Y --export year.png

Three mutually exclusive ways to choose the window (default: --ago 30d):

    --ago <N><unit>     rolling window ending at "now"
                        units (case-sensitive):
                            m = minutes, h = hours, d = days,
                            M = months,  Y = years
                        examples: 5m, 10h, 2d, 4M, 1Y

    --date <range>      absolute date range, inclusive of both endpoints.
                        accepts:
                            YYYY_MM_DD-YYYY_MM_DD   e.g. 2026_04_01-2026_04_30
                            YYYYMMDD-YYYYMMDD       e.g. 20260401-20260430

    --datetime <range>  absolute datetime range, minute precision.
                        format: YYYY_MM_DD-hh:mm-YYYY_MM_DD-hh:mm
                        e.g. 2026_04_01-09:00-2026_04_01-17:30

Absolute --date / --datetime values are interpreted in your local timezone.

The report renders in the terminal. A file is written only when ``--export`` (or
the legacy ``--output``) names one: ``.png`` / ``.pdf`` / ``.svg`` gets the
multi-panel image, ``.json`` / ``.csv`` gets the rows behind it. Either way the
terminal report still prints.

Auth comes from TRACKSPACE_PAT. These env vars override the defaults from /kb:
JIRA_BASE_URL, JIRA_AUTH_TYPE, JIRA_API_VERSION, JIRA_EMAIL (basic auth only).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import pandas as pd
from rich.console import Console

from trackspace.auth import auth_status, read_pat
from trackspace.client import TrackspaceClient
from trackspace.errors import (
    AuthError,
    ConfigurationError,
    ForbiddenError,
    TrackspaceError,
)
from trackspace.kb import KnowledgeBase, load_kb
from trackspace.ui import chrome, prompts
from trackspace.ui.prompts import Choice

from . import terminal
from .fetch import UserNotFoundError, fetch_recent_worklogs
from .window import (
    DEFAULT_AGO,
    LOCAL_TZ,
    parse_ago,
    parse_datetime_range,
    resolve_window,
    window_from_ago,
    window_from_dates,
)

TOOL_NAME = "Trackspace worklog visualiser"
USER_AGENT = "trackspace-worklog-visualizer"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2

IMAGE_SUFFIXES = {".png", ".pdf", ".svg"}
DATA_SUFFIXES = {".json", ".csv"}

#: Presets offered in the interactive window picker.
WINDOW_PRESETS = (
    ("Last 7 days", "7d"),
    ("Last 30 days", "30d"),
    ("Last 90 days", "90d"),
    ("Last 12 months", "12M"),
)


@dataclass(frozen=True)
class Report:
    """One rendering of one window for one user."""

    start: datetime
    end: datetime
    label: str
    user: str | None


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="worklog-visualizer",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_report_flags(parser)
    subparsers = parser.add_subparsers(dest="command")
    _add_report_flags(
        subparsers.add_parser("report", help="render one report and exit"), on_subcommand=True
    )
    return parser


def _add_report_flags(parser: argparse.ArgumentParser, *, on_subcommand: bool = False) -> None:
    """Attach the report flags to a parser.

    The subcommand suppresses its defaults so that `--ago 7d report` keeps the
    window instead of having it overwritten by the subparser's own default.
    """
    default = argparse.SUPPRESS if on_subcommand else None
    parser.add_argument(
        "--user", default=default, help="username of another user (default: yourself)"
    )
    parser.add_argument(
        "--export",
        type=Path,
        default=default,
        metavar="PATH",
        help="also write the report to PATH (.png/.pdf/.svg image, or .json/.csv rows)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default,
        metavar="PATH",
        help=argparse.SUPPRESS,  # legacy spelling of --export, still honoured
    )
    parser.add_argument(
        "--show",
        action="store_true",
        default=argparse.SUPPRESS if on_subcommand else False,
        help="open the exported image in a matplotlib window as well",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        default=argparse.SUPPRESS if on_subcommand else False,
        help=argparse.SUPPRESS,  # legacy no-op: nothing opens unless --show
    )

    win = parser.add_argument_group("window").add_mutually_exclusive_group()
    win.add_argument(
        "--ago",
        default=default,
        help=(
            "rolling window ending at now. <number><unit>, "
            "units (case-sensitive): m=minutes, h=hours, d=days, "
            "M=months, Y=years. examples: 5m, 10h, 2d, 4M, 1Y. "
            f"(default if no window flag is given: {DEFAULT_AGO})"
        ),
    )
    win.add_argument(
        "--date",
        dest="date_range",
        default=default,
        help=(
            "absolute date range, both endpoints inclusive. "
            "format: YYYY_MM_DD-YYYY_MM_DD or YYYYMMDD-YYYYMMDD "
            "(e.g. 2026_04_01-2026_04_30 or 20260401-20260430). "
            "interpreted in local time."
        ),
    )
    win.add_argument(
        "--datetime",
        dest="datetime_range",
        default=default,
        help=(
            "absolute datetime range, both endpoints inclusive. "
            "format: YYYY_MM_DD-hh:mm-YYYY_MM_DD-hh:mm "
            "(e.g. 2026_04_01-09:00-2026_04_01-17:30). "
            "interpreted in local time."
        ),
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def export_path(args: argparse.Namespace) -> Path | None:
    """``--export``, or the legacy ``--output`` spelling."""
    return cast("Path | None", args.export or args.output)


def window_flags_given(args: argparse.Namespace) -> bool:
    return bool(args.ago or args.date_range or args.datetime_range)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
def make_client(kb: KnowledgeBase) -> TrackspaceClient:
    credentials = read_pat(kb=kb)
    if credentials is None:
        raise ConfigurationError(
            f"{kb.token_env} environment variable is not set. "
            f"Export your Trackspace Personal Access Token first "
            f"(generate one at {kb.pat_profile_url})."
        )
    return TrackspaceClient(
        credentials.token,
        base_url=os.environ.get("JIRA_BASE_URL", kb.base_url),
        api_version=os.environ.get("JIRA_API_VERSION", kb.api_version),
        kb=kb,
        auth_type=os.environ.get("JIRA_AUTH_TYPE", "bearer").lower(),
        email=os.environ.get("JIRA_EMAIL"),
        user_agent=USER_AGENT,
    )


# ---------------------------------------------------------------------------
# Running one report
# ---------------------------------------------------------------------------
def run_report(
    console: Console,
    client: TrackspaceClient,
    report: Report,
    *,
    destination: Path | None = None,
    show: bool = False,
    summary: chrome.RunSummary | None = None,
) -> pd.DataFrame:
    """Fetch, render in the terminal, and export only if asked to."""
    warnings: list[str] = []
    with chrome.LiveStatus(console, "Searching issues") as status:

        def on_status(message: str) -> None:
            status.update(message)

        def on_warning(message: str) -> None:
            warnings.append(message)
            status.log("warning", message)

        df, who = fetch_recent_worklogs(
            client,
            report.user,
            report.start,
            report.end,
            on_status=on_status,
            on_warning=on_warning,
        )
        status.update(f"Fetched {len(df)} worklogs", worklogs=len(df))

    if summary is not None:
        summary.replace("worklogs", len(df))
    terminal.render_report(console, df, report.start, report.end, who, report.label)

    details: list[str] = []
    if destination is not None:
        written = export(df, report, destination, who=who, show=show)
        details.append(f"Written to {written}")
    if warnings:
        details.append(f"{len(warnings)} worklogs could not be read and were skipped.")

    total = float(df["hours"].sum()) if not df.empty else 0.0
    chrome.final(
        console,
        "success" if len(df) else "warning",
        f"{len(df)} worklog entries totalling {total:.1f}h in the {report.label} "
        f"({report.start.isoformat(timespec='seconds')} → "
        f"{report.end.isoformat(timespec='seconds')})",
        details=details,
    )
    return df


def export(
    df: pd.DataFrame,
    report: Report,
    destination: Path,
    *,
    who: str,
    show: bool = False,
) -> Path:
    """Write the report to a file. Images go through matplotlib, rows do not."""
    suffix = destination.suffix.lower()
    if suffix not in IMAGE_SUFFIXES | DATA_SUFFIXES:
        raise ConfigurationError(
            f"cannot export to {destination.name}: use one of "
            f"{', '.join(sorted(IMAGE_SUFFIXES | DATA_SUFFIXES))}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)

    if suffix in DATA_SUFFIXES:
        rows: list[dict[str, Any]] = [
            {str(column): value for column, value in record.items()}
            for record in df.to_dict(orient="records")
        ]
        for row in rows:
            if isinstance(row.get("date"), date):
                row["date"] = row["date"].isoformat()
        if suffix == ".csv":
            with destination.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(df.columns))
                writer.writeheader()
                writer.writerows(rows)
        else:
            destination.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return destination

    # Images need matplotlib, so it is imported only on this path — and pinned to
    # a non-interactive backend unless a window was explicitly asked for.
    import matplotlib

    if not show:
        matplotlib.use("Agg")
    from . import figure

    fig = figure.build_figure(df, report.start, report.end, who, report.label)
    fig.savefig(destination, dpi=150, bbox_inches="tight", facecolor="white")
    if show:
        import matplotlib.pyplot as plt

        plt.show()
    return destination


# ---------------------------------------------------------------------------
# Interactive session
# ---------------------------------------------------------------------------
def interactive(
    console: Console,
    client: TrackspaceClient,
    report: Report,
    summary: chrome.RunSummary,
) -> int:
    prompts.require_tty()
    run_report(console, client, report, summary=summary)

    while True:
        choice = prompts.select(
            "What next?",
            choices=[
                Choice("Refresh this report", "refresh"),
                Choice(f"Time window  ({report.label})", "window"),
                Choice(f"Whose worklogs  ({report.user or 'me'})", "user"),
                Choice("Export…", "export"),
                Choice("Quit", "quit"),
            ],
        )
        if prompts.is_back(choice):
            # Nothing above the top menu to go back to.
            continue
        if choice == "quit":
            chrome.final(console, "info", "Session ended")
            return EXIT_OK
        try:
            if choice == "refresh":
                run_report(console, client, report, summary=summary)
            elif choice == "window":
                report = _pick_window(console, report)
                run_report(console, client, report, summary=summary)
            elif choice == "user":
                report = _pick_user(console, client, report)
                run_report(console, client, report, summary=summary)
            elif choice == "export":
                destination = Path(
                    prompts.text(
                        "Write to (.png/.pdf/.svg/.json/.csv)",
                        default="worklogs.png",
                        validate=_validate_export_path,
                    ).strip()
                ).expanduser()
                run_report(console, client, report, destination=destination, summary=summary)
        except TrackspaceError as exc:
            chrome.notice(console, "error", str(exc))
        # A session must survive one bad step: report it and stay in the menu.
        except Exception as exc:
            chrome.notice(console, "error", f"{type(exc).__name__}: {exc}")


def _validate_export_path(value: str) -> bool | str:
    suffix = Path(value.strip()).suffix.lower()
    if suffix in IMAGE_SUFFIXES | DATA_SUFFIXES:
        return True
    return f"Use one of {', '.join(sorted(IMAGE_SUFFIXES | DATA_SUFFIXES))}"


def _pick_window(console: Console, report: Report) -> Report:
    choice = prompts.select(
        "Time window",
        choices=[
            *[Choice(label, ("ago", spec)) for label, spec in WINDOW_PRESETS],
            Choice("Custom rolling window (e.g. 6h, 2d, 4M)", ("custom-ago", "")),
            Choice("Custom date range", ("dates", "")),
            Choice("Custom datetime range", ("datetimes", "")),
            Choice("Back", ("back", "")),
        ],
        allow_back=True,
    )
    if prompts.is_back(choice):
        return report
    kind, spec = choice
    if kind == "back":
        return report

    if kind == "ago":
        start, end, label = window_from_ago(spec)
    elif kind == "custom-ago":
        value = prompts.text("Window (e.g. 5m, 10h, 2d, 4M, 1Y)", default="30d", validate=_ago_ok)
        start, end, label = window_from_ago(value.strip())
    elif kind == "dates":
        first = prompts.text("From (YYYY-MM-DD)", validate=prompts.validate_date)
        last = prompts.text("To (YYYY-MM-DD)", validate=prompts.validate_date)
        start, end, label = window_from_dates(
            date.fromisoformat(first.strip()), date.fromisoformat(last.strip())
        )
    else:
        value = prompts.text(
            "Range (YYYY_MM_DD-hh:mm-YYYY_MM_DD-hh:mm)",
            default="2026_04_01-09:00-2026_04_01-17:30",
            validate=_datetime_range_ok,
        )
        first_dt, last_dt = parse_datetime_range(value.strip())
        start = first_dt.replace(tzinfo=LOCAL_TZ)
        end = last_dt.replace(tzinfo=LOCAL_TZ)
        label = "selected range"

    if end < start:
        chrome.notice(console, "warning", "The window ends before it starts — nothing will match.")
    return replace(report, start=start, end=end, label=label)


def _ago_ok(value: str) -> bool | str:
    try:
        parse_ago(value)
    except argparse.ArgumentTypeError as exc:
        return str(exc)
    return True


def _datetime_range_ok(value: str) -> bool | str:
    try:
        parse_datetime_range(value)
    except argparse.ArgumentTypeError as exc:
        return str(exc)
    return True


def _pick_user(console: Console, client: TrackspaceClient, report: Report) -> Report:
    choice = prompts.select(
        "Whose worklogs",
        choices=[
            Choice("Mine", "me"),
            Choice("Someone else…", "other"),
            Choice("Back", "back"),
        ],
        allow_back=True,
    )
    if prompts.is_back(choice) or choice == "back":
        return report
    if choice == "me":
        return replace(report, user=None)

    query = prompts.text(
        "Username, email or part of a display name", validate=prompts.validate_nonempty
    ).strip()
    found = client.find_user(query)
    if found is None:
        chrome.notice(console, "error", f"No user found matching '{query}'.")
        return report
    chrome.notice(console, "success", f"Selected {found.get('displayName') or query}")
    return replace(report, user=query)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    chrome.install_sigint_handler()
    args = parse_args(argv)
    console = chrome.make_console()
    summary = chrome.RunSummary()

    try:
        with chrome.cancellable(console, summary):
            kb = load_kb()
            start, end, label = resolve_window(args)
            report = Report(start=start, end=end, label=label, user=args.user)

            present, auth_label = auth_status(kb=kb)
            chrome.header(
                console,
                tool=TOOL_NAME,
                instance=os.environ.get("JIRA_BASE_URL", kb.base_url),
                auth_present=present,
                auth_label=auth_label,
                rows=[
                    (
                        "window",
                        f"{report.label}  ({start:%-d %b %Y %H:%M} → {end:%-d %b %Y %H:%M})",
                    ),
                    ("user", report.user or "me"),
                ],
            )

            client = make_client(kb)
            with client:
                interactive_run = (
                    args.command is None
                    and not window_flags_given(args)
                    and export_path(args) is None
                    and chrome.is_interactive()
                )
                if interactive_run:
                    return interactive(console, client, report, summary)
                run_report(
                    console,
                    client,
                    report,
                    destination=export_path(args),
                    show=args.show,
                    summary=summary,
                )
                return EXIT_OK
    except (AuthError, ForbiddenError) as exc:
        message = (
            "Auth failed (401). Check TRACKSPACE_PAT."
            if isinstance(exc, AuthError)
            else "Forbidden (403). Token lacks permission for this resource."
        )
        chrome.final(console, "error", message)
        return EXIT_CONFIG
    except (ConfigurationError, UserNotFoundError) as exc:
        chrome.final(console, "error", str(exc))
        return EXIT_CONFIG
    except TrackspaceError as exc:
        chrome.final(console, "error", f"Trackspace request failed: {exc}", summary=summary)
        return EXIT_FAILED


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
