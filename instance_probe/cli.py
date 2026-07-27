"""Trackspace instance probe (NT-1).

A read-only diagnostic run that resolves custom field ids, issue types,
workflow states, the CLOPSSEC project's real name, the actual error body
shape and (carefully) rate-limit behaviour — the facts
``kb/trackspace.json`` currently marks unknown or inferred. See
``kb/proposals/capability-audit.md`` (NT-1, KBW-1 through KBW-6).

This tool never writes ``kb/trackspace.json``. It only ever performs GET
requests (enforced by :func:`instance_probe.probe.assert_get_only` before any
step runs); the rate-limit step additionally never retries and is capped::

    python -m instance_probe
    python -m instance_probe --only fields --only project
    python -m instance_probe --rate-limit-burst 10 --export findings.json
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from trackspace.auth import auth_status, require_pat
from trackspace.client import TrackspaceClient
from trackspace.errors import ConfigurationError, TrackspaceError
from trackspace.kb import KnowledgeBase, load_kb
from trackspace.ui import chrome

from . import probe, render

TOOL_NAME = "Trackspace instance probe"
USER_AGENT = "trackspace-instance-probe"

EXIT_OK = 0
EXIT_FAILURES = 1
EXIT_CONFIG = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instance-probe",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--only",
        action="append",
        choices=probe.STEP_NAMES,
        metavar="STEP",
        help="run only this step; repeatable. Default: every step except rate-limit.",
    )
    parser.add_argument(
        "--rate-limit-burst",
        type=int,
        nargs="?",
        const=probe.RATE_LIMIT_DEFAULT_BURST,
        default=None,
        metavar="N",
        help=(
            "opt-in: also run the rate-limit step, N rapid unretried GET /myself "
            f"calls (default {probe.RATE_LIMIT_DEFAULT_BURST} if given with no "
            f"value; hard cap {probe.RATE_LIMIT_HARD_CAP})"
        ),
    )
    parser.add_argument(
        "--export",
        type=Path,
        metavar="PATH",
        help="also write the findings report as JSON to PATH",
    )
    return parser


def _selected_steps(args: argparse.Namespace) -> tuple[list[str], int | None]:
    """The step order for this run, and the rate-limit burst if it applies.

    ``--rate-limit-burst`` implies including the ``rate-limit`` step even
    without ``--only``; asking for the step via ``--only`` without the flag
    falls back to the default burst — either way the step is genuinely
    opt-in, never part of a plain, flag-free run.
    """
    steps = list(dict.fromkeys(args.only)) if args.only else list(probe.DEFAULT_STEPS)
    if args.rate_limit_burst is not None and "rate-limit" not in steps:
        steps.append("rate-limit")
    burst = args.rate_limit_burst
    if "rate-limit" in steps and burst is None:
        burst = probe.RATE_LIMIT_DEFAULT_BURST
    return steps, burst


def make_client(kb: KnowledgeBase, token: str) -> TrackspaceClient:
    return TrackspaceClient(token, kb=kb, base_url=kb.base_url, user_agent=USER_AGENT)


def main(argv: Sequence[str] | None = None) -> int:
    chrome.install_sigint_handler()
    parser = build_parser()
    args = parser.parse_args(argv)

    console = chrome.make_console()
    summary = chrome.RunSummary()

    try:
        with chrome.cancellable(console, summary):
            kb = load_kb()
            probe.assert_get_only(kb)  # fail fast if a KB entry stops being a GET
            steps, burst = _selected_steps(args)

            present, label = auth_status()
            chrome.header(
                console,
                tool=TOOL_NAME,
                instance=kb.base_url,
                auth_present=present,
                auth_label=label,
                rows=[("steps", ", ".join(steps))],
            )

            credentials = require_pat()
            client = make_client(kb, credentials.token)

            with client, chrome.LiveStatus(console, "Probing Trackspace") as status:
                ok_count = 0
                failed_count = 0

                def _on_step(name: str, finding: probe.Finding) -> None:
                    nonlocal ok_count, failed_count
                    if finding.ok:
                        ok_count += 1
                        status.log("success", f"{name}: {finding.summary}")
                    else:
                        failed_count += 1
                        status.log("error", f"{name}: {finding.summary}")
                    status.update(f"Probing Trackspace [{name}]", ok=ok_count, failed=failed_count)

                findings_report = probe.run_probe(
                    client, kb, steps=steps, rate_limit_burst=burst, on_step=_on_step
                )

            summary.replace("steps", len(findings_report.findings))
            summary.replace("failed", failed_count)

            render.render_terminal(console, findings_report)

            details: list[str] = []
            if args.export is not None:
                render.export_json(findings_report, args.export)
                details.append(f"Written to {args.export}")

            failed = [f for f in findings_report.findings if not f.ok]
            total = len(findings_report.findings)
            if failed:
                chrome.final(
                    console,
                    "warning",
                    f"{total - len(failed)}/{total} steps ok — "
                    f"{len(failed)} failed: {', '.join(f.name for f in failed)}",
                    details=details,
                    summary=summary,
                )
                return EXIT_FAILURES

            chrome.final(
                console,
                "success",
                f"{total}/{total} steps ok",
                details=details,
                summary=summary,
            )
            return EXIT_OK
    except ConfigurationError as exc:
        chrome.final(console, "error", str(exc))
        return EXIT_CONFIG
    except TrackspaceError as exc:
        chrome.final(console, "error", f"Trackspace request failed: {exc}", summary=summary)
        return EXIT_FAILURES


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
