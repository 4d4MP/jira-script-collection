"""LIB-10: the shared canonical export shape, and LIB-2's progress wiring."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import fixture_router, make_client
from trackspace import export
from trackspace.errors import ConfigurationError
from trackspace.kb import KnowledgeBase
from worklog_scheduler import dashboard as dash
from worklog_visualizer import fetch

BERLIN = ZoneInfo("Europe/Berlin")


def rows() -> list[dict[str, Any]]:
    return [
        export.canonical_row(
            issue="CLOPSSEC-41456",
            summary="Recurring meetings and internal coordination",
            date="2026-04-01",
            hours=0.5,
            comment="Daily",
            author="Adam Papp",
        ),
        export.canonical_row(
            issue="CLOPSSEC-41501",
            summary="Suspicious login from 10.0.0.5",
            date="2026-04-08",
            hours=1.5,
        ),
    ]


def test_canonical_row_fills_uncollected_fields_with_empty_strings() -> None:
    row = rows()[1]
    assert row["comment"] == ""
    assert row["author"] == ""
    assert list(row) == list(export.CANONICAL_FIELDS)


def test_json_round_trip_carries_the_schema_marker(tmp_path: Path) -> None:
    destination = tmp_path / "rows.json"
    export.write_canonical(rows(), destination)
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["schema"] == export.CANONICAL_SCHEMA
    assert export.read_canonical(destination) == rows()


def test_csv_round_trip_keeps_the_fixed_column_order(tmp_path: Path) -> None:
    destination = tmp_path / "rows.csv"
    export.write_canonical(rows(), destination)
    with destination.open(encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
    assert header == list(export.CANONICAL_FIELDS)
    read_back = export.read_canonical(destination)
    assert [row["issue"] for row in read_back] == ["CLOPSSEC-41456", "CLOPSSEC-41501"]


def test_unknown_suffix_is_a_configuration_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="use .json or .csv"):
        export.write_canonical(rows(), tmp_path / "rows.txt")
    with pytest.raises(ConfigurationError, match="cannot read"):
        export.read_canonical(tmp_path / "rows.txt")


def test_reading_a_non_canonical_json_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "other.json"
    source.write_text(json.dumps([{"key": "X-1"}]), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="not a canonical worklog export"):
        export.read_canonical(source)


def test_existing_export_shapes_are_untouched_by_lib10() -> None:
    """The historical shapes stay exactly as pinned; canonical is a third shape."""
    assert set(export.CANONICAL_FIELDS) == {
        "issue",
        "summary",
        "date",
        "hours",
        "comment",
        "author",
    }
    # scheduler export fields (dashboard.export) and visualiser COLUMNS keep
    # their own names — asserted by their own pinned tests; here we just pin
    # that the canonical shape is distinct from both.
    assert "key" not in export.CANONICAL_FIELDS
    assert "ticket_id" not in export.CANONICAL_FIELDS


# ---- LIB-2: the search progress callback reaches both tools ----------------
def test_scheduler_fetch_reports_search_progress(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb))
    seen: list[tuple[int, int]] = []
    dash.fetch_worklogs(
        client,
        date(2026, 4, 1),
        date(2026, 4, 30),
        on_search=lambda done, total: seen.append((done, total)),
    )
    # The fake server pages the 5-issue search as 3 then 2.
    assert seen == [(3, 5), (5, 5)]


def test_visualizer_fetch_reports_search_progress(kb: KnowledgeBase) -> None:
    client, _ = make_client(kb, fixture_router(kb))
    messages: list[str] = []
    fetch.fetch_recent_worklogs(
        client,
        None,
        datetime(2026, 4, 1, tzinfo=BERLIN),
        datetime(2026, 4, 30, 23, 59, tzinfo=BERLIN),
        on_status=messages.append,
    )
    assert any("Searching issues" in m and "[3/5]" in m for m in messages)
    assert any("[5/5]" in m for m in messages)
