"""The canonical worklog-row export shape (LIB-10).

The two tools grew incompatible export schemas independently: the scheduler's
dashboard writes ``key``/``summary``/``date``/``hours``/``comment`` and the
visualiser writes ``date``/``ticket_id``/``summary``/``hours``/``author`` —
different ticket-id names, and each carries a field the other lacks. Renaming
either would break its pinned export tests, so this module defines a **third**
shape both tools can *optionally* emit, and neither emits by default:

    issue, summary, date, hours, comment, author

A field a tool does not collect is written as the empty string, never omitted,
so every canonical file has the same columns regardless of which tool wrote it.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .errors import ConfigurationError

#: Column order of the canonical shape. Fixed — consumers may rely on it.
CANONICAL_FIELDS = ("issue", "summary", "date", "hours", "comment", "author")

#: Schema marker written into JSON exports so a future shape can be told apart.
CANONICAL_SCHEMA = "trackspace-worklog-rows/1"


def canonical_row(
    *,
    issue: str,
    summary: str,
    date: str,
    hours: float,
    comment: str = "",
    author: str = "",
) -> dict[str, Any]:
    """One row in the canonical shape; uncollected fields stay empty strings."""
    return {
        "issue": issue,
        "summary": summary,
        "date": date,
        "hours": round(hours, 3),
        "comment": comment,
        "author": author,
    }


def write_canonical(rows: list[dict[str, Any]], destination: Path) -> Path:
    """Write canonical rows to ``.json`` or ``.csv``.

    JSON gets a ``{"schema": ..., "rows": [...]}`` envelope so the file is
    self-describing; CSV gets the fixed column order with a header row.
    """
    suffix = destination.suffix.lower()
    if suffix not in {".json", ".csv"}:
        raise ConfigurationError(
            f"cannot write canonical rows to {destination.name}: use .json or .csv"
        )
    for row in rows:
        missing = set(CANONICAL_FIELDS) - set(row)
        if missing:  # pragma: no cover - programming error in a caller
            raise ConfigurationError(f"canonical row is missing {sorted(missing)}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".csv":
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(CANONICAL_FIELDS))
            writer.writeheader()
            writer.writerows(rows)
    else:
        destination.write_text(
            json.dumps({"schema": CANONICAL_SCHEMA, "rows": rows}, indent=2),
            encoding="utf-8",
        )
    return destination


def read_canonical(source: Path) -> list[dict[str, Any]]:
    """Read rows back from either canonical format, validating the shape."""
    suffix = source.suffix.lower()
    if suffix == ".csv":
        with source.open(encoding="utf-8") as handle:
            raw_rows = list(csv.DictReader(handle))
    elif suffix == ".json":
        document = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(document, dict) or document.get("schema") != CANONICAL_SCHEMA:
            raise ConfigurationError(
                f"{source.name} is not a canonical worklog export "
                f"(expected schema {CANONICAL_SCHEMA!r})"
            )
        raw_rows = document.get("rows", [])
    else:
        raise ConfigurationError(f"cannot read canonical rows from {source.name}")

    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        missing = set(CANONICAL_FIELDS) - set(raw)
        if missing:
            raise ConfigurationError(f"{source.name}: row is missing {sorted(missing)}")
        rows.append({field: raw[field] for field in CANONICAL_FIELDS})
    return rows
