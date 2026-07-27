#!/usr/bin/env python3
"""Trackspace issue companion.

Companion actions on one Trackspace issue: show its summary/status/changelog,
list (never execute) its transitions, and read/write comments, attachments,
issue links and remote links.

Interactive by default::

    python -m issue_companion

Every interactive choice also has a flag, so the same tool runs unattended::

    python -m issue_companion show CLOPSSEC-41456 --changelog --attachments --links
    python -m issue_companion transitions CLOPSSEC-41456
    python -m issue_companion comment CLOPSSEC-41456 list
    python -m issue_companion comment CLOPSSEC-41456 add --body "Logged via script"
    python -m issue_companion attach CLOPSSEC-41456 upload report.png --yes
    python -m issue_companion link CLOPSSEC-41456 add --type Blocks --to CLOPSSEC-41501

Transition *execution* is deliberately not built here — see
``kb/build-notes.md``: the POST is gated on a probe run that records a real
transition graph for this instance. ``transitions`` only lists what is
available.

Binary attachment download is out of scope too (``request_json`` is JSON-only;
see ``kb/build-notes.md``'s design decisions) — ``attach`` can list, upload and
delete, not fetch content.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rich.console import Console

from trackspace.auth import auth_status, require_pat
from trackspace.client import TrackspaceClient
from trackspace.errors import ConfigurationError, TrackspaceError
from trackspace.kb import load_kb
from trackspace.ui import chrome, prompts, tables
from trackspace.ui.prompts import Choice

TOOL_NAME = "Trackspace issue companion"
USER_AGENT = "trackspace-issue-companion"

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_CONFIG = 2

#: NT-2: read-only listing only. No execute path exists until a probe run
#: records a real transition graph for this instance (kb/build-notes.md).
GATED_TRANSITION_NOTE = (
    "Executing a transition is not supported yet — gated on a probe run that "
    "records a real transition graph for this instance (see kb/build-notes.md)."
)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issue-companion",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base-url", metavar="URL", default=None, help="Trackspace base URL")
    subparsers = parser.add_subparsers(dest="command")

    show = subparsers.add_parser("show", help="issue summary and status")
    show.add_argument("issue", metavar="ISSUE")
    show.add_argument("--changelog", action="store_true", help="also show the history table")
    show.add_argument("--attachments", action="store_true", help="also list attachments")
    show.add_argument("--links", action="store_true", help="also show issue and remote links")

    transitions = subparsers.add_parser(
        "transitions", help="list the transitions available on an issue (read-only)"
    )
    transitions.add_argument("issue", metavar="ISSUE")

    comment = subparsers.add_parser("comment", help="list, add, update or delete comments")
    comment.add_argument("issue", metavar="ISSUE")
    comment_sub = comment.add_subparsers(dest="comment_action", required=True)
    comment_sub.add_parser("list", help="list comments")
    c_add = comment_sub.add_parser("add", help="add a comment")
    c_add.add_argument("--body", required=True, metavar="TEXT")
    c_add.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    c_update = comment_sub.add_parser("update", help="update a comment")
    c_update.add_argument("--id", required=True, dest="comment_id", metavar="N")
    c_update.add_argument("--body", required=True, metavar="TEXT")
    c_update.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    c_delete = comment_sub.add_parser("delete", help="delete a comment")
    c_delete.add_argument("--id", required=True, dest="comment_id", metavar="N")
    c_delete.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    attach = subparsers.add_parser("attach", help="list, upload or delete attachments")
    attach.add_argument("issue", metavar="ISSUE")
    attach_sub = attach.add_subparsers(dest="attach_action", required=True)
    attach_sub.add_parser("list", help="list attachments")
    a_upload = attach_sub.add_parser("upload", help="upload a file")
    a_upload.add_argument("path", type=Path, metavar="PATH")
    a_upload.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    a_delete = attach_sub.add_parser("delete", help="delete an attachment")
    a_delete.add_argument("--id", required=True, dest="attachment_id", metavar="N")
    a_delete.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    link = subparsers.add_parser("link", help="list, add or delete issue/remote links")
    link.add_argument("issue", metavar="ISSUE")
    link_sub = link.add_subparsers(dest="link_action", required=True)
    link_sub.add_parser("list", help="list issue links and remote links")
    l_add = link_sub.add_parser("add", help="link to another issue")
    l_add.add_argument("--type", required=True, dest="type_name", metavar="NAME")
    l_add.add_argument("--to", required=True, dest="to_key", metavar="KEY")
    l_add.add_argument(
        "--inward",
        action="store_true",
        help="this issue is the inward side of the relationship (default: outward)",
    )
    l_add.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    l_addr = link_sub.add_parser("add-remote", help="add a remote (non-Jira) link")
    l_addr.add_argument("--url", required=True)
    l_addr.add_argument("--title", required=True)
    l_addr.add_argument("--global-id", dest="global_id", default=None, metavar="ID")
    l_addr.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    l_del = link_sub.add_parser("delete", help="remove an issue link")
    l_del.add_argument("--id", required=True, dest="link_id", metavar="N")
    l_del.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    l_delr = link_sub.add_parser("delete-remote", help="remove a remote link")
    l_delr.add_argument("--id", required=True, dest="link_id", metavar="N")
    l_delr.add_argument("--yes", action="store_true", help="skip the confirmation prompt")

    return parser


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def _format_size(num_bytes: Any) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return str(num_bytes)
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"  # pragma: no cover - only reached above 1 GB


def _print_changelog(console: Console, changelog: dict[str, Any]) -> None:
    rows: list[tuple[str, str, str, str]] = []
    for history in changelog.get("histories") or []:
        author = (history.get("author") or {}).get("displayName", "")
        created = history.get("created", "")
        for item in history.get("items") or []:
            rows.append(
                (
                    author,
                    created,
                    item.get("field", ""),
                    f"{item.get('fromString', '')} → {item.get('toString', '')}",
                )
            )
    if not rows:
        tables.empty_notice(console, "No changelog entries.")
        return
    fixed = [16, 22, 12]
    tables.render_table(
        console,
        [
            tables.Column("Author", width=16),
            tables.Column("Date", width=22),
            tables.Column("Field", width=12),
            tables.Column("Change", width=tables.flex_width(console, fixed)),
        ],
        rows,
        title="Changelog",
    )


def _print_attachments(console: Console, attachments: list[dict[str, Any]]) -> None:
    if not attachments:
        tables.empty_notice(console, "No attachments.")
        return
    fixed = [8, 10, 16, 22]
    rows = [
        (
            attachment.get("id", ""),
            attachment.get("filename", ""),
            _format_size(attachment.get("size", 0)),
            (attachment.get("author") or {}).get("displayName", ""),
            attachment.get("created", ""),
        )
        for attachment in attachments
    ]
    tables.render_table(
        console,
        [
            tables.Column("Id", width=8),
            tables.Column("Filename", width=tables.flex_width(console, fixed)),
            tables.Column("Size", width=10, justify="right"),
            tables.Column("Author", width=16),
            tables.Column("Created", width=22),
        ],
        rows,
        title="Attachments",
    )


def _print_links(
    console: Console, issuelinks: list[dict[str, Any]], remote: list[dict[str, Any]]
) -> None:
    if issuelinks:
        fixed = [8, 18, 14]
        rows = []
        for link in issuelinks:
            link_type = link.get("type") or {}
            if "inwardIssue" in link:
                other = link["inwardIssue"]
                relationship = link_type.get("outward", "relates to")
            else:
                other = link.get("outwardIssue") or {}
                relationship = link_type.get("inward", "relates to")
            rows.append(
                (
                    link.get("id", ""),
                    relationship,
                    other.get("key", ""),
                    (other.get("fields") or {}).get("summary", ""),
                )
            )
        tables.render_table(
            console,
            [
                tables.Column("Id", width=8),
                tables.Column("Relationship", width=18),
                tables.Column("Issue", width=14),
                tables.Column("Summary", width=tables.flex_width(console, fixed)),
            ],
            rows,
            title="Issue links",
        )
    else:
        tables.empty_notice(console, "No issue links.")

    if remote:
        fixed = [8, 28]
        remote_rows = [
            (
                str(link.get("id", "")),
                (link.get("object") or {}).get("title", ""),
                (link.get("object") or {}).get("url", ""),
            )
            for link in remote
        ]
        tables.render_table(
            console,
            [
                tables.Column("Id", width=8),
                tables.Column("Title", width=28),
                tables.Column("URL", width=tables.flex_width(console, fixed)),
            ],
            remote_rows,
            title="Remote links",
        )
    else:
        tables.empty_notice(console, "No remote links.")


def _print_comments(console: Console, comments: list[dict[str, Any]]) -> None:
    if not comments:
        tables.empty_notice(console, "No comments.")
        return
    fixed = [8, 16, 20]
    rows = [
        (
            comment.get("id", ""),
            (comment.get("author") or {}).get("displayName", ""),
            comment.get("created", ""),
            (comment.get("body") or "").replace("\n", " "),
        )
        for comment in comments
    ]
    tables.render_table(
        console,
        [
            tables.Column("Id", width=8),
            tables.Column("Author", width=16),
            tables.Column("Created", width=20),
            tables.Column("Body", width=tables.flex_width(console, fixed)),
        ],
        rows,
        title="Comments",
    )


def _print_transitions(console: Console, transitions: list[dict[str, Any]]) -> None:
    if not transitions:
        tables.empty_notice(console, "No transitions available.")
        return
    fixed = [6, 28]
    tables.render_table(
        console,
        [
            tables.Column("Id", width=6),
            tables.Column("Name", width=28),
            tables.Column("Target status", width=tables.flex_width(console, fixed)),
        ],
        [
            (
                transition.get("id", ""),
                transition.get("name", ""),
                (transition.get("to") or {}).get("name", ""),
            )
            for transition in transitions
        ],
        title="Transitions",
    )


def _confirm(message: str, *, assume_yes: bool) -> bool:
    """True if the caller may proceed: either ``--yes`` was passed, or the
    user confirmed interactively. Never silently proceeds without either."""
    if assume_yes:
        return True
    prompts.require_tty()
    return prompts.confirm(message, default=False)


# ---------------------------------------------------------------------------
# Commands: show / transitions
# ---------------------------------------------------------------------------
def do_show(
    console: Console,
    client: TrackspaceClient,
    issue_key: str,
    *,
    changelog: bool,
    attachments: bool,
    links: bool,
    summary: chrome.RunSummary,
) -> int:
    expand = "changelog" if changelog else None
    with chrome.LiveStatus(console, f"Fetching {issue_key}") as status:
        issue = client.issue_get(issue_key, expand=expand)
        remote = client.remote_links(issue_key) if links else []
        status.update(f"Fetched {issue_key}")

    fields = issue.get("fields") or {}
    status_name = (fields.get("status") or {}).get("name", "(unknown)")
    assignee = fields.get("assignee")
    assignee_name = assignee.get("displayName", "") if assignee else "(unassigned)"
    console.print(
        chrome.key_value_panel(
            issue_key,
            [
                ("summary", fields.get("summary", "")),
                ("status", status_name),
                ("assignee", assignee_name),
            ],
            console,
        )
    )
    if changelog:
        _print_changelog(console, issue.get("changelog") or {})
    if attachments:
        _print_attachments(console, fields.get("attachment") or [])
    if links:
        _print_links(console, fields.get("issuelinks") or [], remote)

    summary.replace("issue", issue_key)
    chrome.final(console, "success", f"{issue_key} — {fields.get('summary', '')}")
    return EXIT_OK


def do_transitions(console: Console, client: TrackspaceClient, issue_key: str) -> int:
    with chrome.LiveStatus(console, f"Fetching transitions for {issue_key}") as status:
        transitions = client.issue_transitions(issue_key)
        status.update(f"Fetched {len(transitions)} transitions")
    _print_transitions(console, transitions)
    chrome.notice(console, "info", GATED_TRANSITION_NOTE)
    chrome.final(console, "success", f"{len(transitions)} transitions listed for {issue_key}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Commands: comment
# ---------------------------------------------------------------------------
def do_comment_list(console: Console, client: TrackspaceClient, issue_key: str) -> int:
    with chrome.LiveStatus(console, f"Fetching comments for {issue_key}") as status:
        comments = client.comments(issue_key)
        status.update(f"Fetched {len(comments)} comments")
    _print_comments(console, comments)
    chrome.final(console, "success", f"{len(comments)} comments on {issue_key}")
    return EXIT_OK


def do_comment_add(
    console: Console, client: TrackspaceClient, issue_key: str, body: str, *, assume_yes: bool
) -> int:
    if not _confirm(f"Add a comment to {issue_key}?", assume_yes=assume_yes):
        chrome.final(console, "warning", "Cancelled — nothing was posted.")
        return EXIT_OK
    with chrome.LiveStatus(console, f"Posting comment to {issue_key}"):
        created = client.add_comment(issue_key, body)
    chrome.final(console, "success", f"Comment {created.get('id', '?')} added to {issue_key}")
    return EXIT_OK


def do_comment_update(
    console: Console,
    client: TrackspaceClient,
    issue_key: str,
    comment_id: str,
    body: str,
    *,
    assume_yes: bool,
) -> int:
    if not _confirm(f"Update comment {comment_id} on {issue_key}?", assume_yes=assume_yes):
        chrome.final(console, "warning", "Cancelled — nothing was changed.")
        return EXIT_OK
    with chrome.LiveStatus(console, f"Updating comment {comment_id}"):
        client.update_comment(issue_key, comment_id, body)
    chrome.final(console, "success", f"Comment {comment_id} updated on {issue_key}")
    return EXIT_OK


def do_comment_delete(
    console: Console, client: TrackspaceClient, issue_key: str, comment_id: str, *, assume_yes: bool
) -> int:
    if not _confirm(f"Delete comment {comment_id} on {issue_key}?", assume_yes=assume_yes):
        chrome.final(console, "warning", "Cancelled — nothing was deleted.")
        return EXIT_OK
    with chrome.LiveStatus(console, f"Deleting comment {comment_id}"):
        client.delete_comment(issue_key, comment_id)
    chrome.final(console, "success", f"Comment {comment_id} deleted from {issue_key}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Commands: attach
# ---------------------------------------------------------------------------
def do_attach_list(console: Console, client: TrackspaceClient, issue_key: str) -> int:
    with chrome.LiveStatus(console, f"Fetching attachments for {issue_key}") as status:
        issue = client.issue_get(issue_key, fields="attachment")
        status.update("Fetched attachments")
    attachments = (issue.get("fields") or {}).get("attachment") or []
    _print_attachments(console, attachments)
    chrome.final(console, "success", f"{len(attachments)} attachments on {issue_key}")
    return EXIT_OK


def do_attach_upload(
    console: Console,
    client: TrackspaceClient,
    issue_key: str,
    path: Path,
    *,
    assume_yes: bool,
) -> int:
    if not path.is_file():
        raise ConfigurationError(f"no such file: {path}")

    with chrome.LiveStatus(console, "Checking attachment limits"):
        meta = client.attachment_meta()
    if not meta.get("enabled", True):
        raise ConfigurationError("attachments are disabled on this Trackspace instance.")
    limit = meta.get("uploadLimit")
    size = path.stat().st_size
    if isinstance(limit, int | float) and size > limit:
        raise ConfigurationError(
            f"{path} is {size} bytes, over the {int(limit)}-byte upload limit for this instance."
        )

    if not _confirm(f"Upload {path.name} ({size} bytes) to {issue_key}?", assume_yes=assume_yes):
        chrome.final(console, "warning", "Cancelled — nothing was uploaded.")
        return EXIT_OK

    with chrome.LiveStatus(console, f"Uploading {path.name}"):
        created = client.upload_attachment(issue_key, path)
    chrome.final(
        console, "success", f"Uploaded {created.get('filename', path.name)} to {issue_key}"
    )
    return EXIT_OK


def do_attach_delete(
    console: Console,
    client: TrackspaceClient,
    issue_key: str,
    attachment_id: str,
    *,
    assume_yes: bool,
) -> int:
    if not _confirm(f"Delete attachment {attachment_id} from {issue_key}?", assume_yes=assume_yes):
        chrome.final(console, "warning", "Cancelled — nothing was deleted.")
        return EXIT_OK
    with chrome.LiveStatus(console, f"Deleting attachment {attachment_id}"):
        client.delete_attachment(attachment_id)
    chrome.final(console, "success", f"Attachment {attachment_id} deleted from {issue_key}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Commands: link
# ---------------------------------------------------------------------------
def do_link_list(console: Console, client: TrackspaceClient, issue_key: str) -> int:
    with chrome.LiveStatus(console, f"Fetching links for {issue_key}") as status:
        issue = client.issue_get(issue_key)
        remote = client.remote_links(issue_key)
        status.update("Fetched links")
    issuelinks = (issue.get("fields") or {}).get("issuelinks") or []
    _print_links(console, issuelinks, remote)
    chrome.final(
        console,
        "success",
        f"{len(issuelinks)} issue links, {len(remote)} remote links on {issue_key}",
    )
    return EXIT_OK


def do_link_add(
    console: Console,
    client: TrackspaceClient,
    issue_key: str,
    type_name: str,
    to_key: str,
    *,
    inward: bool,
    assume_yes: bool,
) -> int:
    with chrome.LiveStatus(console, "Checking link types"):
        link_types = client.link_types()
    valid: set[str] = {name for t in link_types if (name := t.get("name"))}
    if type_name not in valid:
        raise ConfigurationError(
            f"unknown link type {type_name!r}. Valid types: {', '.join(sorted(valid))}."
        )
    if inward:
        inward_key, outward_key = issue_key, to_key
    else:
        outward_key, inward_key = issue_key, to_key

    if not _confirm(f"Link {issue_key} to {to_key} as {type_name!r}?", assume_yes=assume_yes):
        chrome.final(console, "warning", "Cancelled — nothing was linked.")
        return EXIT_OK
    with chrome.LiveStatus(console, "Creating link"):
        client.create_issue_link(type_name, inward_key, outward_key)
    chrome.final(console, "success", f"Linked {issue_key} to {to_key} as {type_name!r}")
    return EXIT_OK


def do_link_add_remote(
    console: Console,
    client: TrackspaceClient,
    issue_key: str,
    url: str,
    title: str,
    *,
    global_id: str | None,
    assume_yes: bool,
) -> int:
    if not _confirm(f"Add remote link {title!r} to {issue_key}?", assume_yes=assume_yes):
        chrome.final(console, "warning", "Cancelled — nothing was linked.")
        return EXIT_OK
    with chrome.LiveStatus(console, "Creating remote link"):
        created = client.create_remote_link(issue_key, url, title, global_id=global_id)
    chrome.final(console, "success", f"Remote link {created.get('id', '?')} added to {issue_key}")
    return EXIT_OK


def do_link_delete(
    console: Console, client: TrackspaceClient, issue_key: str, link_id: str, *, assume_yes: bool
) -> int:
    if not _confirm(f"Remove link {link_id} from {issue_key}?", assume_yes=assume_yes):
        chrome.final(console, "warning", "Cancelled — nothing was removed.")
        return EXIT_OK
    with chrome.LiveStatus(console, f"Removing link {link_id}"):
        client.delete_issue_link(link_id)
    chrome.final(console, "success", f"Link {link_id} removed")
    return EXIT_OK


def do_link_delete_remote(
    console: Console, client: TrackspaceClient, issue_key: str, link_id: str, *, assume_yes: bool
) -> int:
    if not _confirm(f"Remove remote link {link_id} from {issue_key}?", assume_yes=assume_yes):
        chrome.final(console, "warning", "Cancelled — nothing was removed.")
        return EXIT_OK
    with chrome.LiveStatus(console, f"Removing remote link {link_id}"):
        client.delete_remote_link(issue_key, link_id)
    chrome.final(console, "success", f"Remote link {link_id} removed from {issue_key}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def make_client(base_url: str) -> TrackspaceClient:
    credentials = require_pat()
    return TrackspaceClient(credentials.token, base_url=base_url, user_agent=USER_AGENT)


def _dispatch(
    console: Console,
    client: TrackspaceClient,
    args: argparse.Namespace,
    *,
    summary: chrome.RunSummary,
) -> int:
    command = args.command
    if command == "show":
        return do_show(
            console,
            client,
            args.issue,
            changelog=args.changelog,
            attachments=args.attachments,
            links=args.links,
            summary=summary,
        )
    if command == "transitions":
        return do_transitions(console, client, args.issue)
    if command == "comment":
        if args.comment_action == "list":
            return do_comment_list(console, client, args.issue)
        if args.comment_action == "add":
            return do_comment_add(console, client, args.issue, args.body, assume_yes=args.yes)
        if args.comment_action == "update":
            return do_comment_update(
                console, client, args.issue, args.comment_id, args.body, assume_yes=args.yes
            )
        if args.comment_action == "delete":
            return do_comment_delete(
                console, client, args.issue, args.comment_id, assume_yes=args.yes
            )
    if command == "attach":
        if args.attach_action == "list":
            return do_attach_list(console, client, args.issue)
        if args.attach_action == "upload":
            return do_attach_upload(console, client, args.issue, args.path, assume_yes=args.yes)
        if args.attach_action == "delete":
            return do_attach_delete(
                console, client, args.issue, args.attachment_id, assume_yes=args.yes
            )
    if command == "link":
        if args.link_action == "list":
            return do_link_list(console, client, args.issue)
        if args.link_action == "add":
            return do_link_add(
                console,
                client,
                args.issue,
                args.type_name,
                args.to_key,
                inward=args.inward,
                assume_yes=args.yes,
            )
        if args.link_action == "add-remote":
            return do_link_add_remote(
                console,
                client,
                args.issue,
                args.url,
                args.title,
                global_id=args.global_id,
                assume_yes=args.yes,
            )
        if args.link_action == "delete":
            return do_link_delete(console, client, args.issue, args.link_id, assume_yes=args.yes)
        if args.link_action == "delete-remote":
            return do_link_delete_remote(
                console, client, args.issue, args.link_id, assume_yes=args.yes
            )
    raise ConfigurationError(f"unknown command {command!r}")  # pragma: no cover - argparse guards


# ---------------------------------------------------------------------------
# Interactive session
# ---------------------------------------------------------------------------
def interactive(console: Console, base_url: str, *, summary: chrome.RunSummary) -> int:
    prompts.require_tty()
    issue_key = prompts.text("Issue key", validate=prompts.validate_issue_key).strip()

    with make_client(base_url) as client:
        while True:
            choice = prompts.select(
                f"Trackspace issue companion — {issue_key}",
                choices=[
                    Choice("Show issue", "show"),
                    Choice("Show changelog", "changelog"),
                    Choice("Attachments", "attach"),
                    Choice("Comments", "comment"),
                    Choice("Links", "link"),
                    Choice("Transitions (read-only)", "transitions"),
                    Choice(f"Change issue key  ({issue_key})", "change-issue"),
                    Choice("Quit", "quit"),
                ],
                allow_back=True,
            )
            if prompts.is_back(choice):
                continue
            if choice == "quit":
                break
            try:
                if choice == "show":
                    do_show(
                        console,
                        client,
                        issue_key,
                        changelog=False,
                        attachments=False,
                        links=False,
                        summary=summary,
                    )
                elif choice == "changelog":
                    do_show(
                        console,
                        client,
                        issue_key,
                        changelog=True,
                        attachments=False,
                        links=False,
                        summary=summary,
                    )
                elif choice == "attach":
                    _interactive_attach(console, client, issue_key)
                elif choice == "comment":
                    _interactive_comment(console, client, issue_key)
                elif choice == "link":
                    _interactive_link(console, client, issue_key)
                elif choice == "transitions":
                    do_transitions(console, client, issue_key)
                elif choice == "change-issue":
                    issue_key = prompts.text(
                        "Issue key", default=issue_key, validate=prompts.validate_issue_key
                    ).strip()
            except TrackspaceError as exc:
                chrome.notice(console, "error", str(exc))
            except Exception as exc:  # keep the session alive on any surprise
                chrome.notice(console, "error", f"{type(exc).__name__}: {exc}")

    chrome.final(console, "info", "Session ended")
    return EXIT_OK


def _interactive_attach(console: Console, client: TrackspaceClient, issue_key: str) -> None:
    action = prompts.select(
        "Attachments",
        choices=[
            Choice("List", "list"),
            Choice("Upload", "upload"),
            Choice("Delete", "delete"),
            Choice("Back", "back"),
        ],
        allow_back=True,
    )
    if prompts.is_back(action) or action == "back":
        return
    if action == "list":
        do_attach_list(console, client, issue_key)
    elif action == "upload":
        path_str = prompts.text("File path", validate=prompts.validate_nonempty)
        do_attach_upload(
            console, client, issue_key, Path(path_str.strip()).expanduser(), assume_yes=False
        )
    elif action == "delete":
        attachment_id = prompts.text("Attachment id", validate=prompts.validate_nonempty)
        do_attach_delete(console, client, issue_key, attachment_id.strip(), assume_yes=False)


def _interactive_comment(console: Console, client: TrackspaceClient, issue_key: str) -> None:
    action = prompts.select(
        "Comments",
        choices=[
            Choice("List", "list"),
            Choice("Add", "add"),
            Choice("Update", "update"),
            Choice("Delete", "delete"),
            Choice("Back", "back"),
        ],
        allow_back=True,
    )
    if prompts.is_back(action) or action == "back":
        return
    if action == "list":
        do_comment_list(console, client, issue_key)
    elif action == "add":
        body = prompts.text("Comment text", validate=prompts.validate_nonempty)
        do_comment_add(console, client, issue_key, body, assume_yes=False)
    elif action == "update":
        comment_id = prompts.text("Comment id", validate=prompts.validate_nonempty)
        body = prompts.text("New comment text", validate=prompts.validate_nonempty)
        do_comment_update(console, client, issue_key, comment_id.strip(), body, assume_yes=False)
    elif action == "delete":
        comment_id = prompts.text("Comment id", validate=prompts.validate_nonempty)
        do_comment_delete(console, client, issue_key, comment_id.strip(), assume_yes=False)


def _interactive_link(console: Console, client: TrackspaceClient, issue_key: str) -> None:
    action = prompts.select(
        "Links",
        choices=[
            Choice("List", "list"),
            Choice("Add issue link", "add"),
            Choice("Add remote link", "add-remote"),
            Choice("Delete issue link", "delete"),
            Choice("Delete remote link", "delete-remote"),
            Choice("Back", "back"),
        ],
        allow_back=True,
    )
    if prompts.is_back(action) or action == "back":
        return
    if action == "list":
        do_link_list(console, client, issue_key)
    elif action == "add":
        type_name = prompts.text("Link type name", validate=prompts.validate_nonempty)
        to_key = prompts.text("Other issue key", validate=prompts.validate_issue_key)
        inward = prompts.confirm("Is this issue the inward side?", default=False)
        do_link_add(
            console,
            client,
            issue_key,
            type_name.strip(),
            to_key.strip(),
            inward=inward,
            assume_yes=False,
        )
    elif action == "add-remote":
        url = prompts.text("URL", validate=prompts.validate_nonempty)
        title = prompts.text("Title", validate=prompts.validate_nonempty)
        global_id = prompts.text("Global id (optional)")
        do_link_add_remote(
            console,
            client,
            issue_key,
            url.strip(),
            title.strip(),
            global_id=global_id.strip() or None,
            assume_yes=False,
        )
    elif action == "delete":
        link_id = prompts.text("Link id", validate=prompts.validate_nonempty)
        do_link_delete(console, client, issue_key, link_id.strip(), assume_yes=False)
    elif action == "delete-remote":
        link_id = prompts.text("Remote link id", validate=prompts.validate_nonempty)
        do_link_delete_remote(console, client, issue_key, link_id.strip(), assume_yes=False)


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
            kb = load_kb()  # fail fast and loudly if the knowledge base is unreachable
            base_url = (args.base_url or kb.base_url).rstrip("/")
            present, label = auth_status()
            chrome.header(
                console,
                tool=TOOL_NAME,
                instance=base_url,
                auth_present=present,
                auth_label=label,
            )

            if args.command is None:
                if chrome.is_interactive():
                    return interactive(console, base_url, summary=summary)
                parser.print_help()
                chrome.notice(console, "info", "No terminal attached — pass a subcommand instead.")
                return EXIT_CONFIG

            with make_client(base_url) as client:
                return _dispatch(console, client, args, summary=summary)
    except ConfigurationError as exc:
        chrome.final(console, "error", str(exc))
        return EXIT_CONFIG
    except TrackspaceError as exc:
        chrome.final(console, "error", f"Trackspace request failed: {exc}", summary=summary)
        return EXIT_FAILURES


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
