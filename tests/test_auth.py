"""PAT discovery, and the guarantee that the token never leaks into output."""

from __future__ import annotations

import pytest

from trackspace.auth import auth_status, read_pat, require_pat
from trackspace.errors import ConfigurationError
from trackspace.kb import KnowledgeBase


def test_primary_variable_wins(kb: KnowledgeBase) -> None:
    creds = read_pat({"TRACKSPACE_PAT": "primary", "JIRA_API_TOKEN": "fallback"}, kb)
    assert creds is not None
    assert creds.token == "primary"
    assert creds.source_env == "TRACKSPACE_PAT"


def test_fallback_variable(kb: KnowledgeBase) -> None:
    creds = read_pat({"JIRA_API_TOKEN": "fallback"}, kb)
    assert creds is not None
    assert creds.source_env == "JIRA_API_TOKEN"


def test_blank_is_treated_as_missing(kb: KnowledgeBase) -> None:
    assert read_pat({"TRACKSPACE_PAT": "   "}, kb) is None


def test_missing_pat_explains_how_to_set_one(kb: KnowledgeBase) -> None:
    with pytest.raises(ConfigurationError) as caught:
        require_pat({}, kb)
    message = str(caught.value)
    assert "TRACKSPACE_PAT is not set" in message
    assert "ViewProfile.jspa" in message


def test_credentials_never_render_the_token(kb: KnowledgeBase) -> None:
    creds = read_pat({"TRACKSPACE_PAT": "s3cr3t-value"}, kb)
    assert creds is not None
    assert "s3cr3t-value" not in repr(creds)
    assert "s3cr3t-value" not in str(creds)
    assert "redacted" in repr(creds)


def test_auth_status_labels(kb: KnowledgeBase) -> None:
    present, label = auth_status({"TRACKSPACE_PAT": "s3cr3t"}, kb)
    assert present
    assert label == "present (from TRACKSPACE_PAT)"
    assert "s3cr3t" not in label

    present, label = auth_status({}, kb)
    assert not present
    assert label == "missing (TRACKSPACE_PAT not set)"
