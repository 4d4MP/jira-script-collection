"""Terminal rendering and JSON export for a :class:`~instance_probe.probe.FindingsReport`.

Both forms carry the same information — the terminal table is always printed;
the JSON file is written only when the caller passes an export path (house
export rule, see ``CLAUDE.md``'s CLI contract).
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from trackspace.ui import tables

from .probe import FindingsReport

_FIXED_COLUMN_WIDTHS = [13, 6, 18]


def render_terminal(console: Console, report: FindingsReport) -> None:
    columns = [
        tables.Column("Step", width=13),
        tables.Column("Status", width=6),
        tables.Column("Endpoint", width=18),
        tables.Column("Summary", width=tables.flex_width(console, _FIXED_COLUMN_WIDTHS)),
    ]
    rows = [
        (finding.name, "ok" if finding.ok else "FAILED", finding.endpoint, finding.summary)
        for finding in report.findings
    ]
    tables.render_table(
        console,
        columns,
        rows,
        title="Probe findings",
        caption=f"probed at {report.probed_at}  ·  {report.note}",
    )


def export_json(report: FindingsReport, path: Path) -> None:
    """Write the findings report as JSON. Only called when ``--export`` is passed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
