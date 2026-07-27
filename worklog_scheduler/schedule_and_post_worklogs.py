#!/usr/bin/env python3
"""Trackspace worklog scheduler.

Plan recurring and one-off meeting time over a date range, preview every entry it
implies, post them to a Trackspace issue, and see what you have logged.

Interactive by default::

    python -m worklog_scheduler

Every interactive choice also has a flag, so the same tool runs unattended::

    python -m worklog_scheduler preview --range this-month
    python -m worklog_scheduler submit --live --yes
    python -m worklog_scheduler dashboard --from 2026-04-01 --to 2026-04-30

Behaviour is inherited from the original Tkinter tool (``work_log.py``): the same
schedule expansion, the same dry-run-by-default posting, the same dashboard
figures, and the same config file at ``~/.jira_worklog_manager.json``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from rich.console import Console

from trackspace.auth import auth_status, read_pat, require_pat
from trackspace.client import TrackspaceClient
from trackspace.errors import ConfigurationError, TrackspaceError
from trackspace.kb import load_kb
from trackspace.ui import chrome, prompts, tables
from trackspace.ui.prompts import Choice

from . import dashboard as dash
from .clock import today_local
from .config import CONFIG_PATH, WEEKDAY_NAMES, OneOffMeeting, RecurringMeeting, ScheduleConfig
from .schedule import (
    QUICK_RANGES,
    WorklogEntry,
    build_entries,
    parse_iso_date,
    parse_oneoff_spec,
    parse_recurring_spec,
    quick_range,
    total_minutes,
)

TOOL_NAME = "Trackspace worklog scheduler"
USER_AGENT = "trackspace-worklog-scheduler"

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_CONFIG = 2


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="worklog-scheduler",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="config file to use")
    parser.add_argument("--no-save", action="store_true", help="never write the config file")
    _add_schedule_flags(parser)

    subparsers = parser.add_subparsers(dest="command")

    preview = subparsers.add_parser("preview", help="show the entries the schedule implies")
    _add_schedule_flags(preview)

    submit = subparsers.add_parser("submit", help="post the entries to Trackspace")
    _add_schedule_flags(submit)
    mode = submit.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", dest="dry_run", action="store_true", help="do not post (default)"
    )
    mode.add_argument("--live", dest="dry_run", action="store_false", help="actually post")
    submit.set_defaults(dry_run=None)
    submit.add_argument("--yes", action="store_true", help="skip the live-submit confirmation")

    board = subparsers.add_parser("dashboard", help="what you have logged in the range")
    _add_schedule_flags(board)
    board.add_argument(
        "--export",
        type=Path,
        metavar="PATH",
        help="also write the fetched worklogs to a .json or .csv file",
    )

    config = subparsers.add_parser("config", help="show, export or load the saved config")
    _add_schedule_flags(config)
    config.add_argument("--export", type=Path, metavar="PATH", help="write the config to PATH")
    config.add_argument("--load", type=Path, metavar="PATH", help="replace the config from PATH")
    config.add_argument("--save", action="store_true", help="write the config back to its file")

    return parser


def _add_schedule_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("schedule")
    group.add_argument("--issue", metavar="KEY", help="issue to book time against")
    group.add_argument("--base-url", metavar="URL", help="Trackspace base URL")
    group.add_argument("--timezone", metavar="TZ", help="timezone for computed start times")
    group.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="range start")
    group.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="range end")
    group.add_argument("--range", dest="quick", choices=QUICK_RANGES, help="preset range")
    group.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="YYYY-MM-DD",
        help="skip a date; repeatable",
    )
    group.add_argument(
        "--recurring",
        action="append",
        default=None,
        metavar="DAYS@HH:MM+MIN=COMMENT",
        help="replace the recurring meetings, e.g. MON-FRI@10:00+30=Daily; repeatable",
    )
    group.add_argument(
        "--oneoff",
        action="append",
        default=None,
        metavar="DATE@HH:MM+MIN=COMMENT",
        help="add a one-off meeting, e.g. 2026-04-03@13:00+30=Workshop; repeatable",
    )


def apply_flags(cfg: ScheduleConfig, args: argparse.Namespace) -> None:
    """Overlay command-line flags onto the loaded config."""
    if getattr(args, "issue", None):
        cfg.issue_key = args.issue.strip()
    if getattr(args, "base_url", None):
        cfg.trackspace_base = args.base_url.rstrip("/")
    if getattr(args, "timezone", None):
        cfg.timezone = args.timezone.strip()
    if getattr(args, "quick", None):
        start, end = quick_range(args.quick)
        cfg.start_date, cfg.end_date = start.isoformat(), end.isoformat()
    if getattr(args, "date_from", None):
        cfg.start_date = parse_iso_date(args.date_from, "--from").isoformat()
    if getattr(args, "date_to", None):
        cfg.end_date = parse_iso_date(args.date_to, "--to").isoformat()
    if getattr(args, "exclude", None):
        cfg.exclude_dates = sorted(
            {parse_iso_date(value, "--exclude").isoformat() for value in args.exclude}
        )
    if getattr(args, "recurring", None):
        cfg.recurring = [parse_recurring_spec(spec) for spec in args.recurring]
    if getattr(args, "oneoff", None):
        cfg.oneoffs.extend(parse_oneoff_spec(spec) for spec in args.oneoff)
        cfg.sort_oneoffs()
    dry_run = getattr(args, "dry_run", None)
    if dry_run is not None:
        cfg.dry_run = bool(dry_run)


# ---------------------------------------------------------------------------
# Shared rendering
# ---------------------------------------------------------------------------
def print_header(console: Console, cfg: ScheduleConfig) -> bool:
    present, label = auth_status()
    chrome.header(
        console,
        tool=TOOL_NAME,
        instance=cfg.trackspace_base,
        auth_present=present,
        auth_label=label,
        rows=[
            ("issue", cfg.issue_key or "(not set)"),
            ("range", f"{cfg.start_date} → {cfg.end_date}"),
            ("mode", "dry run" if cfg.dry_run else "LIVE"),
        ],
    )
    return present


def print_preview(console: Console, entries: Sequence[WorklogEntry]) -> None:
    if not entries:
        tables.empty_notice(console, "No entries match the current configuration.")
        return
    tables.render_table(
        console,
        [
            tables.Column("Date", width=10),
            tables.Column("Day", width=3),
            tables.Column("Time", width=5),
            tables.Column("Duration", width=8, justify="right"),
            tables.Column("Comment", width=tables.flex_width(console, [10, 3, 5, 8])),
        ],
        [
            (
                f"{entry.started:%Y-%m-%d}",
                f"{entry.started:%a}",
                f"{entry.started:%H:%M}",
                f"{entry.duration_min} min",
                entry.comment,
            )
            for entry in entries
        ],
        title="Planned worklogs",
        caption=_totals_line(list(entries)),
    )


def _totals_line(entries: list[WorklogEntry]) -> str:
    minutes = total_minutes(entries)
    return f"{len(entries)} entries  ·  total {minutes} min  =  {minutes / 60:.2f} h"


def print_schedule(console: Console, cfg: ScheduleConfig) -> None:
    if cfg.recurring:
        tables.render_table(
            console,
            [
                tables.Column("Days", width=14),
                tables.Column("Time", width=5),
                tables.Column("Duration", width=8, justify="right"),
                tables.Column("Comment", width=tables.flex_width(console, [14, 5, 8])),
            ],
            [
                (m.weekdays_str(), m.time_str(), f"{m.duration_min} min", m.comment)
                for m in cfg.recurring
            ],
            title="Recurring meetings",
        )
    else:
        tables.empty_notice(console, "No recurring meetings configured.")

    if cfg.oneoffs:
        tables.render_table(
            console,
            [
                tables.Column("Date", width=10),
                tables.Column("Time", width=5),
                tables.Column("Duration", width=8, justify="right"),
                tables.Column("Comment", width=tables.flex_width(console, [10, 5, 8])),
            ],
            [(o.date, o.time_str(), f"{o.duration_min} min", o.comment) for o in cfg.oneoffs],
            title="One-off meetings",
        )
    if cfg.exclude_dates:
        console.print(
            chrome.key_value_panel(
                "Excluded dates", [("dates", ", ".join(cfg.exclude_dates))], console
            )
        )


def make_client(cfg: ScheduleConfig) -> TrackspaceClient:
    credentials = require_pat()
    return TrackspaceClient(
        credentials.token,
        base_url=cfg.trackspace_base,
        user_agent=USER_AGENT,
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def do_preview(console: Console, cfg: ScheduleConfig) -> int:
    entries = build_entries(cfg)
    print_preview(console, entries)
    if entries:
        chrome.final(
            console,
            "info",
            f"{len(entries)} worklogs planned for {cfg.issue_key}",
            details=[
                _totals_line(entries),
                "Nothing has been posted — run `submit --live` when the preview looks right.",
            ],
        )
    else:
        chrome.final(
            console,
            "warning",
            "Nothing to post",
            details=["No entries match the current configuration."],
        )
    return EXIT_OK


def do_submit(
    console: Console,
    cfg: ScheduleConfig,
    *,
    assume_yes: bool,
    summary: chrome.RunSummary,
) -> int:
    if not cfg.issue_key:
        raise ConfigurationError("no issue key set. Pass --issue, e.g. --issue CLOPSSEC-41456.")

    entries = build_entries(cfg)
    if not entries:
        chrome.final(
            console,
            "warning",
            "Nothing to post",
            details=["No entries match the current configuration."],
        )
        return EXIT_OK

    minutes = total_minutes(entries)
    if not cfg.dry_run and read_pat() is None:
        raise ConfigurationError(
            "posting needs a Personal Access Token. Set TRACKSPACE_PAT before running live."
        )

    print_preview(console, entries)

    if not cfg.dry_run and not assume_yes:
        prompts.require_tty()
        approved = prompts.confirm(
            f"Post {len(entries)} worklogs to {cfg.issue_key}? "
            f"Total {minutes / 60:.2f} h. This is LIVE and cannot be undone by this tool.",
            default=False,
        )
        if not approved:
            chrome.final(console, "warning", "Cancelled — nothing was posted.")
            return EXIT_OK

    console.print()
    console.print(
        chrome.key_value_panel(
            "Submit",
            [
                ("issue", cfg.issue_key),
                ("mode", "DRY RUN" if cfg.dry_run else "LIVE"),
                ("entries", str(len(entries))),
                ("total", f"{minutes} min  =  {minutes / 60:.2f} h"),
            ],
            console,
        )
    )

    summary.replace("entries", len(entries))
    posted = 0
    failures = 0

    if cfg.dry_run:
        for entry in entries:
            console.print(chrome.status_text("muted", f"[dry] {_entry_line(entry)}"))
        summary.replace("posted", 0)
        chrome.final(
            console,
            "info",
            f"Dry run complete — {len(entries)} worklogs would be posted to {cfg.issue_key}",
            details=[
                _totals_line(entries),
                "Nothing was sent. Re-run with `submit --live` to post.",
            ],
        )
        return EXIT_OK

    with make_client(cfg) as client, chrome.LiveStatus(console, "Posting worklogs") as status:
        status.update(posted=0, failed=0)
        for entry in entries:
            try:
                created = client.add_worklog(
                    cfg.issue_key,
                    started=entry.started,
                    duration_seconds=entry.duration_min * 60,
                    comment=entry.comment,
                )
            except TrackspaceError as exc:
                failures += 1
                status.log("error", f"{_entry_line(entry)}   ({exc})")
            else:
                posted += 1
                status.log("success", f"{_entry_line(entry)}   (id={created.get('id')})")
            status.update(posted=posted, failed=failures)
            summary.replace("posted", posted)
            summary.replace("failed", failures)

    kind: chrome.Kind = "success" if failures == 0 else "warning"
    chrome.final(
        console,
        kind,
        f"Posted {posted}/{len(entries)} worklogs to {cfg.issue_key}",
        details=[_totals_line(entries)]
        + ([f"{failures} entries failed — see the lines above."] if failures else []),
    )
    return EXIT_OK if failures == 0 else EXIT_FAILURES


def _entry_line(entry: WorklogEntry) -> str:
    return f"{entry.started:%Y-%m-%d %a %H:%M} | {entry.duration_min:>3}m | {entry.comment}"


def do_dashboard(
    console: Console,
    cfg: ScheduleConfig,
    *,
    export_path: Path | None,
    summary: chrome.RunSummary,
) -> int:
    start = parse_iso_date(cfg.start_date, "start date")
    end = parse_iso_date(cfg.end_date, "end date")

    with make_client(cfg) as client, chrome.LiveStatus(console, "Searching issues") as status:
        warnings: list[str] = []

        def on_issues(count: int) -> None:
            status.update(f"Found {count} issues with your worklogs", issues=count)

        def on_issue(index: int, total: int, key: str) -> None:
            status.update(f"Fetching worklogs [{index}/{total}] {key}")

        def on_warning(message: str) -> None:
            warnings.append(message)
            status.log("warning", message)

        records, identity = dash.fetch_worklogs(
            client,
            start,
            end,
            on_issues=on_issues,
            on_issue=on_issue,
            on_warning=on_warning,
        )
        status.update(f"Fetched {len(records)} worklogs", worklogs=len(records))

    summary.replace("worklogs", len(records))
    data = dash.aggregate(records, start, end)
    dash.render(console, data, username=identity.display_name)

    details: list[str] = []
    if export_path is not None:
        dash.export(records, export_path)
        details.append(f"Written to {export_path}")
    if warnings:
        details.append(f"{len(warnings)} issues could not be read fully.")

    chrome.final(
        console,
        "success" if records else "warning",
        f"{len(records)} worklogs, {data.total_hours:.1f}h total, "
        f"{cfg.start_date} → {cfg.end_date}",
        details=details,
    )
    return EXIT_OK


def do_config(
    console: Console,
    cfg: ScheduleConfig,
    args: argparse.Namespace,
    config_path: Path,
) -> int:
    if getattr(args, "load", None):
        loaded = ScheduleConfig.load(args.load)
        cfg.__dict__.update(loaded.__dict__)
        chrome.notice(console, "success", f"Loaded {args.load}")
    print_schedule(console, cfg)
    console.print(
        chrome.key_value_panel(
            "Configuration",
            [
                ("issue", cfg.issue_key),
                ("instance", cfg.trackspace_base),
                ("timezone", cfg.timezone),
                ("range", f"{cfg.start_date} → {cfg.end_date}"),
                ("dry run", "yes" if cfg.dry_run else "no"),
                ("file", str(config_path)),
            ],
            console,
        )
    )
    details = []
    if getattr(args, "export", None):
        args.export.parent.mkdir(parents=True, exist_ok=True)
        cfg.save(args.export)
        details.append(f"Exported to {args.export}")
    if getattr(args, "save", False):
        cfg.save(config_path)
        details.append(f"Saved to {config_path}")
    chrome.final(console, "info", "Configuration shown", details=details)
    return EXIT_OK


# ---------------------------------------------------------------------------
# Interactive session
# ---------------------------------------------------------------------------
def interactive(
    console: Console,
    cfg: ScheduleConfig,
    *,
    config_path: Path,
    save_on_exit: bool,
    summary: chrome.RunSummary,
) -> int:
    prompts.require_tty()
    exit_code = EXIT_OK
    while True:
        choice = prompts.select(
            "What next?",
            choices=[
                Choice("Preview planned worklogs", "preview"),
                Choice("Recurring meetings", "recurring"),
                Choice("One-off meetings", "oneoffs"),
                Choice("Excluded dates", "excludes"),
                Choice(f"Date range  ({cfg.start_date} → {cfg.end_date})", "range"),
                Choice(f"Issue key  ({cfg.issue_key or 'not set'})", "issue"),
                Choice(
                    f"Dry run is {'ON' if cfg.dry_run else 'OFF'} — toggle",
                    "toggle-dry",
                ),
                Choice("Submit worklogs", "submit"),
                Choice("Dashboard", "dashboard"),
                Choice("Save configuration", "save"),
                Choice("Quit", "quit"),
            ],
        )

        if choice == "quit":
            break
        try:
            if choice == "preview":
                do_preview(console, cfg)
            elif choice == "recurring":
                _edit_recurring(cfg)
            elif choice == "oneoffs":
                _edit_oneoffs(cfg)
            elif choice == "excludes":
                _edit_excludes(console, cfg)
            elif choice == "range":
                _edit_range(console, cfg)
            elif choice == "issue":
                cfg.issue_key = prompts.text(
                    "Issue key", default=cfg.issue_key, validate=prompts.validate_issue_key
                ).strip()
            elif choice == "toggle-dry":
                cfg.dry_run = not cfg.dry_run
                chrome.notice(
                    console,
                    "info" if cfg.dry_run else "warning",
                    "Dry run ON — nothing will be posted"
                    if cfg.dry_run
                    else "Dry run OFF — submits are LIVE",
                )
            elif choice == "submit":
                exit_code = do_submit(console, cfg, assume_yes=False, summary=summary)
                if save_on_exit:
                    cfg.save(config_path)
                if not cfg.dry_run and exit_code == EXIT_OK:
                    do_dashboard(console, cfg, export_path=None, summary=summary)
            elif choice == "dashboard":
                do_dashboard(console, cfg, export_path=None, summary=summary)
            elif choice == "save":
                cfg.save(config_path)
                chrome.notice(console, "success", f"Saved to {config_path}")
        except TrackspaceError as exc:
            chrome.notice(console, "error", str(exc))

    if save_on_exit:
        cfg.save(config_path)
    chrome.final(
        console, "info", "Session ended", details=[f"Configuration saved to {config_path}"]
    )
    return exit_code


def _edit_recurring(cfg: ScheduleConfig) -> None:
    while True:
        choices: list[Choice] = [
            Choice(
                f"{m.weekdays_str():<12} {m.time_str()}  {m.duration_min:>3} min  {m.comment}",
                index,
            )
            for index, m in enumerate(cfg.recurring)
        ]
        choices += [Choice("+ Add a recurring meeting", "add"), Choice("Back", "back")]
        selection = prompts.select("Recurring meetings", choices=choices)
        if selection == "back":
            return
        if selection == "add":
            meeting = _prompt_recurring()
            if meeting is not None:
                cfg.recurring.append(meeting)
            continue
        action = prompts.select(
            "This meeting",
            choices=[Choice("Edit", "edit"), Choice("Delete", "delete"), Choice("Back", "back")],
        )
        if action == "edit":
            meeting = _prompt_recurring(cfg.recurring[int(selection)])
            if meeting is not None:
                cfg.recurring[int(selection)] = meeting
        elif action == "delete" and prompts.confirm("Remove this recurring meeting?"):
            del cfg.recurring[int(selection)]


def _prompt_recurring(current: RecurringMeeting | None = None) -> RecurringMeeting | None:
    base = current or RecurringMeeting([0, 1, 2, 3, 4], 10, 0, 30, "")
    selected = prompts.checkbox(
        "Days of week",
        choices=[
            Choice(name, index, checked=index in base.weekdays)
            for index, name in enumerate(WEEKDAY_NAMES)
        ],
    )
    if not selected:
        return None
    clock = prompts.text(
        "Start time (HH:MM)", default=base.time_str(), validate=prompts.validate_time
    )
    duration = prompts.text(
        "Duration (minutes)", default=str(base.duration_min), validate=prompts.validate_positive_int
    )
    comment = prompts.text("Comment", default=base.comment, validate=prompts.validate_nonempty)
    hour, minute = (int(part) for part in clock.split(":"))
    return RecurringMeeting(
        weekdays=sorted(int(day) for day in selected),
        hour=hour,
        minute=minute,
        duration_min=int(duration),
        comment=comment.strip(),
    )


def _edit_oneoffs(cfg: ScheduleConfig) -> None:
    while True:
        cfg.sort_oneoffs()
        choices: list[Choice] = [
            Choice(f"{o.date}  {o.time_str()}  {o.duration_min:>3} min  {o.comment}", index)
            for index, o in enumerate(cfg.oneoffs)
        ]
        choices += [Choice("+ Add a one-off meeting", "add"), Choice("Back", "back")]
        selection = prompts.select("One-off meetings", choices=choices)
        if selection == "back":
            return
        if selection == "add":
            meeting = _prompt_oneoff(default_date=_default_oneoff_date(cfg))
            if meeting is not None:
                cfg.oneoffs.append(meeting)
            continue
        action = prompts.select(
            "This meeting",
            choices=[Choice("Edit", "edit"), Choice("Delete", "delete"), Choice("Back", "back")],
        )
        if action == "edit":
            meeting = _prompt_oneoff(current=cfg.oneoffs[int(selection)])
            if meeting is not None:
                cfg.oneoffs[int(selection)] = meeting
        elif action == "delete" and prompts.confirm("Remove this one-off meeting?"):
            del cfg.oneoffs[int(selection)]


def _default_oneoff_date(cfg: ScheduleConfig) -> date:
    """Today if it falls inside the range, otherwise the range start."""
    try:
        start = date.fromisoformat(cfg.start_date)
        end = date.fromisoformat(cfg.end_date)
    except ValueError:
        return today_local()
    today = today_local()
    return today if start <= today <= end else start


def _prompt_oneoff(
    current: OneOffMeeting | None = None, default_date: date | None = None
) -> OneOffMeeting | None:
    base = current or OneOffMeeting((default_date or today_local()).isoformat(), 13, 0, 30, "")
    day = prompts.text("Date (YYYY-MM-DD)", default=base.date, validate=prompts.validate_date)
    clock = prompts.text(
        "Start time (HH:MM)", default=base.time_str(), validate=prompts.validate_time
    )
    duration = prompts.text(
        "Duration (minutes)", default=str(base.duration_min), validate=prompts.validate_positive_int
    )
    comment = prompts.text("Comment", default=base.comment, validate=prompts.validate_nonempty)
    hour, minute = (int(part) for part in clock.split(":"))
    return OneOffMeeting(
        date=day.strip(),
        hour=hour,
        minute=minute,
        duration_min=int(duration),
        comment=comment.strip(),
    )


def _edit_excludes(console: Console, cfg: ScheduleConfig) -> None:
    while True:
        choices: list[Choice] = [Choice(value, value) for value in cfg.exclude_dates]
        choices += [
            Choice("+ Add an excluded date", "add"),
            Choice("Clear all", "clear"),
            Choice("Back", "back"),
        ]
        selection = prompts.select("Excluded dates", choices=choices)
        if selection == "back":
            return
        if selection == "add":
            value = prompts.text("Date (YYYY-MM-DD)", validate=prompts.validate_date)
            if not cfg.add_exclusion(date.fromisoformat(value.strip())):
                chrome.notice(console, "info", "Already excluded.")
        elif selection == "clear":
            if cfg.exclude_dates and prompts.confirm("Remove all excluded dates?"):
                cfg.exclude_dates = []
        elif prompts.confirm(f"Stop excluding {selection}?", default=True):
            cfg.exclude_dates = [value for value in cfg.exclude_dates if value != selection]


def _edit_range(console: Console, cfg: ScheduleConfig) -> None:
    selection = prompts.select(
        "Date range",
        choices=[
            Choice("This month", "this-month"),
            Choice("Last month", "last-month"),
            Choice("Last 30 days", "last-30"),
            Choice("This week", "this-week"),
            Choice("Custom…", "custom"),
            Choice("Back", "back"),
        ],
    )
    if selection == "back":
        return
    if selection == "custom":
        start = prompts.text(
            "From (YYYY-MM-DD)", default=cfg.start_date, validate=prompts.validate_date
        )
        end = prompts.text("To (YYYY-MM-DD)", default=cfg.end_date, validate=prompts.validate_date)
        cfg.start_date, cfg.end_date = start.strip(), end.strip()
    else:
        first, last = quick_range(selection)
        cfg.start_date, cfg.end_date = first.isoformat(), last.isoformat()
    if cfg.end_date < cfg.start_date:
        chrome.notice(console, "warning", "The range ends before it starts — nothing will match.")
    chrome.notice(console, "info", f"Range set to {cfg.start_date} → {cfg.end_date}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    chrome.install_sigint_handler()
    parser = build_parser()
    args = parser.parse_args(argv)

    console = chrome.make_console()
    summary = chrome.RunSummary()

    try:
        with chrome.cancellable(console, summary):
            load_kb()  # fail fast and loudly if the knowledge base is unreachable
            config_path = Path(args.config).expanduser()
            cfg = ScheduleConfig.load(config_path)
            apply_flags(cfg, args)
            save_on_exit = not args.no_save

            print_header(console, cfg)

            command = args.command
            if command is None:
                if chrome.is_interactive():
                    return interactive(
                        console,
                        cfg,
                        config_path=config_path,
                        save_on_exit=save_on_exit,
                        summary=summary,
                    )
                chrome.notice(
                    console,
                    "info",
                    "No terminal attached — showing the preview. Use a subcommand to do more.",
                )
                return do_preview(console, cfg)

            if command == "preview":
                return do_preview(console, cfg)
            if command == "submit":
                code = do_submit(console, cfg, assume_yes=args.yes, summary=summary)
                if save_on_exit:
                    cfg.save(config_path)
                if not cfg.dry_run and code == EXIT_OK:
                    do_dashboard(console, cfg, export_path=None, summary=summary)
                return code
            if command == "dashboard":
                return do_dashboard(console, cfg, export_path=args.export, summary=summary)
            if command == "config":
                return do_config(console, cfg, args, config_path)
            parser.error(f"unknown command {command!r}")  # pragma: no cover - argparse guards
            return EXIT_CONFIG
    except ConfigurationError as exc:
        chrome.final(console, "error", str(exc))
        return EXIT_CONFIG
    except TrackspaceError as exc:
        chrome.final(console, "error", f"Trackspace request failed: {exc}", summary=summary)
        return EXIT_FAILURES


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
