"""Dashboard fetching, aggregation and terminal rendering, against fixtures only."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
from typing import Any

from tests.conftest import FakeResponse, fixture_router, make_client
from trackspace.kb import KnowledgeBase
from trackspace.ui.theme import make_console
from worklog_scheduler import dashboard as dash

START = date(2026, 4, 1)
END = date(2026, 4, 30)


def fetch(kb: KnowledgeBase, **router_kwargs: Any) -> tuple[list[dash.WorklogRecord], list[str]]:
    client, _ = make_client(kb, fixture_router(kb, **router_kwargs))
    warnings: list[str] = []
    records, _identity = fetch_with_warnings(client, warnings)
    return records, warnings


def fetch_with_warnings(
    client: Any, warnings: list[str]
) -> tuple[list[dash.WorklogRecord], dash.Identity]:
    return dash.fetch_worklogs(client, START, END, on_warning=warnings.append)


def test_fetch_filters_by_author_and_date(kb: KnowledgeBase) -> None:
    records, warnings = fetch(kb)
    assert not warnings
    # Jane Doe's two entries and the 30 March entry are excluded.
    assert all(record.day >= START for record in records)
    assert round(sum(record.hours for record in records), 2) == 9.25
    assert {record.key for record in records} == {
        "CLOPSSEC-41456",
        "CLOPSSEC-41501",
        "CLOPSSEC-41502",
        "CLOPSSEC-41677",
        "CLOPSSEC-41703",
    }


def test_fetch_handles_z_suffixed_timestamps_and_missing_comments(kb: KnowledgeBase) -> None:
    records, _ = fetch(kb)
    by_id = {(record.key, record.day): record for record in records}
    assert (("CLOPSSEC-41456", date(2026, 4, 6))) in by_id  # the ...Z entry
    assert by_id[("CLOPSSEC-41456", date(2026, 4, 7))].comment == ""


def test_one_unreadable_issue_does_not_lose_the_period(kb: KnowledgeBase) -> None:
    records, warnings = fetch(kb, failing_issues=frozenset({"CLOPSSEC-41677"}))
    assert len(warnings) == 1
    assert warnings[0].startswith("failed CLOPSSEC-41677: HTTP 500")
    assert round(sum(record.hours for record in records), 2) == 7.25


def test_empty_result_set(kb: KnowledgeBase) -> None:
    records, warnings = fetch(kb, empty_search=True)
    assert records == []
    assert not warnings


def test_malformed_worklogs_are_skipped_with_warnings(kb: KnowledgeBase) -> None:
    def handler(method: str, url: str, params: Any, body: Any) -> FakeResponse:
        if url.endswith("/myself"):
            return FakeResponse(200, kb.fixture("myself"), url=url, method=method)
        if url.endswith("/search"):
            return FakeResponse(
                200,
                {
                    "startAt": 0,
                    "maxResults": 100,
                    "total": 1,
                    "issues": [{"key": "CLOPSSEC-99999", "fields": {"summary": "Broken"}}],
                },
                url=url,
                method=method,
            )
        return FakeResponse(200, kb.fixture("issue_worklog_malformed"), url=url, method=method)

    client, _ = make_client(kb, handler)
    warnings: list[str] = []
    records, _identity = fetch_with_warnings(client, warnings)

    # Only the entry with an ADF comment survives — with the comment coerced away.
    assert len(records) == 1
    assert records[0].comment == ""
    assert any("unreadable worklog timestamp" in message for message in warnings)
    assert any("without timeSpentSeconds" in message for message in warnings)


def test_aggregation_matches_the_original_figures(kb: KnowledgeBase) -> None:
    records, _ = fetch(kb)
    data = dash.aggregate(records, START, END)

    assert round(data.total_hours, 2) == 9.25
    assert len(data.days) == 30
    assert sorted(day.isoformat() for day in data.active_days) == [
        "2026-04-01",
        "2026-04-02",
        "2026-04-03",
        "2026-04-06",
        "2026-04-07",
        "2026-04-08",
        "2026-04-09",
        "2026-04-10",
        "2026-04-14",
        "2026-04-20",
    ]
    assert round(data.average_active_day, 3) == round(9.25 / 10, 3)
    assert data.visible_keys[0] == "CLOPSSEC-41456"  # 2.5h, the largest
    assert data.hidden_keys == []  # only five tickets, all visible


def test_more_than_eight_tickets_collapse_into_other() -> None:
    records = [
        dash.WorklogRecord(f"KEY-{i}", f"Summary {i}", date(2026, 4, 1), float(20 - i), "")
        for i in range(12)
    ]
    data = dash.aggregate(records, START, END)
    assert len(data.visible_keys) == dash.VISIBLE_TICKETS
    assert len(data.hidden_keys) == 4


def test_top_tickets_group_by_normalised_title(kb: KnowledgeBase) -> None:
    records = [
        dash.WorklogRecord("A-1", "Suspicious login from 10.0.0.5", date(2026, 4, 1), 1.0, ""),
        dash.WorklogRecord("A-2", "Suspicious login from 192.168.1.10", date(2026, 4, 2), 2.0, ""),
        dash.WorklogRecord("B-1", "Firewall rule review", date(2026, 4, 3), 0.5, ""),
    ]
    rows = dash.top_ticket_rows(records)
    assert rows[0] == ("Suspicious login from <IP>  (2 tickets)", 3.0)
    assert rows[1] == ("B-1  ·  Firewall rule review", 0.5)


def test_long_top_ticket_labels_are_truncated() -> None:
    records = [
        dash.WorklogRecord("VERYLONGPROJECTKEY-123456", "x" * 200, date(2026, 4, 1), 1.0, "")
    ]
    label, _hours = dash.top_ticket_rows(records)[0]
    assert len(label) == 70
    assert label.endswith("...")


def test_normalize_title_is_ipv4_only() -> None:
    """The scheduler's normaliser deliberately differs from the visualiser's."""
    assert dash.normalize_title("Alert from 10.0.0.5 now") == "Alert from <IP> now"
    assert dash.normalize_title("Beacon to 2001:db8::1") == "Beacon to 2001:db8::1"


def test_render_prints_the_numbers(kb: KnowledgeBase) -> None:
    records, _ = fetch(kb)
    data = dash.aggregate(records, START, END)
    console = make_console(record=True)
    console.width = 140
    dash.render(console, data, username="Adam Papp")
    output = console.export_text()

    assert "Trackspace worklogs — 01 Apr 2026 – 30 Apr 2026" in output
    assert "Adam Papp" in output
    assert "Total logged" in output
    assert "9.2 h" in output or "9.3 h" in output  # 9.25 rounds either way
    assert "Days logged" in output
    assert "10 / 22 weekdays" in output
    assert "Unique tickets" in output
    assert "CLOPSSEC-41456" in output


def test_render_with_no_records_says_so(kb: KnowledgeBase) -> None:
    console = make_console(record=True)
    console.width = 120
    dash.render(console, dash.aggregate([], START, END))
    assert "No worklogs found in this period." in console.export_text()


def test_export_json_and_csv(kb: KnowledgeBase, tmp_path: Path) -> None:
    records, _ = fetch(kb)

    json_path = tmp_path / "out.json"
    dash.export(records, json_path)
    rows = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(rows) == len(records)
    assert set(rows[0]) == {"key", "summary", "date", "hours", "comment"}

    csv_path = tmp_path / "out.csv"
    dash.export(records, csv_path)
    with csv_path.open(encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == len(records)
