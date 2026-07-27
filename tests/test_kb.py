"""The knowledge base is the single source of instance facts — check it holds."""

from __future__ import annotations

import pytest

from trackspace.errors import ConfigurationError
from trackspace.kb import KnowledgeBase


def test_instance_facts(kb: KnowledgeBase) -> None:
    assert kb.base_url == "https://trackspace.lhsystems.com"
    assert kb.api_version == "2"
    assert kb.api_root == "/rest/api/2"
    assert kb.token_env == "TRACKSPACE_PAT"
    assert kb.token_env_fallbacks == ["JIRA_API_TOKEN"]


def test_every_endpoint_renders_and_has_a_fixture(kb: KnowledgeBase) -> None:
    for name in kb.endpoint_names():
        endpoint = kb.endpoint(name)
        assert endpoint.method in {"GET", "POST"}
        assert endpoint.timeout_s > 0
        assert endpoint.fixture, f"{name} has no offline fixture"
        # Fixture names in the KB point at real files, sometimes via a sibling.
        candidates = [endpoint.fixture]
        if endpoint.fixture.startswith("search_worklog"):
            candidates.append("search_worklog_authors_page2.json")
        for candidate in candidates:
            assert kb.fixture(candidate) is not None


def test_path_rendering_quotes_parameters(kb: KnowledgeBase) -> None:
    endpoint = kb.endpoint("issue_worklogs")
    assert endpoint.render("/rest/api/2", issue_key="CLOPSSEC-41456") == (
        "/issue/CLOPSSEC-41456/worklog"
    )
    # A key that would otherwise break out of the path is escaped.
    assert endpoint.render("/rest/api/2", issue_key="A/B?x=1") == "/issue/A%2FB%3Fx%3D1/worklog"


def test_jql_templates(kb: KnowledgeBase) -> None:
    jql = kb.jql(
        "worklogs_by_current_user_in_range", start_date="2026-04-01", end_date="2026-04-30"
    )
    assert jql == (
        'worklogAuthor = currentUser() AND worklogDate >= "2026-04-01" '
        'AND worklogDate <= "2026-04-30"'
    )
    named = kb.jql(
        "worklogs_by_named_user_in_range",
        username="jane.doe",
        start_date="2026-04-01",
        end_date="2026-04-30",
    )
    assert named.startswith('worklogAuthor = "jane.doe"')


def test_unknown_lookups_are_explicit(kb: KnowledgeBase) -> None:
    with pytest.raises(ConfigurationError):
        kb.endpoint("nope")
    with pytest.raises(ConfigurationError):
        kb.jql("nope")
    with pytest.raises(ConfigurationError, match="unrecorded"):
        kb.field_id("customfield_10001")


def test_custom_fields_are_recorded_as_unknown_not_invented(kb: KnowledgeBase) -> None:
    assert kb.raw["fields"]["custom"] == {}
    assert "UNKNOWN" in kb.raw["fields"]["issue_types_note"]
    assert "UNKNOWN" in kb.raw["fields"]["workflow_states_note"]


def test_defaults_and_worklog_format(kb: KnowledgeBase) -> None:
    assert kb.default("issue_key") == "CLOPSSEC-41456"
    assert kb.default("timezone") == "Europe/Berlin"
    assert kb.worklog_started_format == "%Y-%m-%dT%H:%M:%S.000%z"
    assert kb.page_size("search") == 100
    assert kb.page_size("issue_worklogs") == 1000
    assert kb.field_id("summary") == "summary"


def test_every_fact_carries_provenance(kb: KnowledgeBase) -> None:
    """Nothing in the KB may be stated without a source or an 'inferred' mark."""
    for name, spec in kb.raw["endpoints"].items():
        assert spec.get("source"), f"endpoint {name} has no provenance"
    for name, spec in kb.raw["jql"].items():
        assert spec.get("source"), f"jql {name} has no provenance"
    for name, spec in kb.raw["fields"]["system"].items():
        assert spec.get("source"), f"field {name} has no provenance"
