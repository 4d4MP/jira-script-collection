"""The probe's steps: what each one calls, how it turns a response into a
:class:`Finding`, and the read-only guard that runs before any of them.

Every fact this module produces is a declared unknown in ``kb/trackspace.json``
today (``fields.custom``, ``fields.issue_types_note``,
``fields.workflow_states_note``, ``projects.CLOPSSEC.name``,
``errors.body_shape``, ``errors.rate_limiting`` — see the KB's own ``source``
fields). This module only ever *reads*: it never edits the knowledge base,
never POSTs, and the rate-limit step is opt-in and hard-capped.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import requests

from trackspace.client import TrackspaceClient
from trackspace.errors import ApiError, ConfigurationError, TrackspaceError
from trackspace.kb import KnowledgeBase

#: What each step calls. Checked by :func:`assert_get_only` before anything runs
#: and used to compute the default step order (dict order is insertion order).
STEP_ENDPOINTS: dict[str, tuple[str, ...]] = {
    "fields": ("field_list",),
    "project": ("project_get",),
    "createmeta": ("issue_createmeta",),
    "statuses": ("status_list",),
    "transitions": ("issue_transitions",),
    "permissions": ("my_permissions",),
    "error-shape": ("issue_get",),
    "rate-limit": ("myself",),
}

STEP_NAMES: tuple[str, ...] = tuple(STEP_ENDPOINTS)

#: Every step except the opt-in rate-limit burst.
DEFAULT_STEPS: tuple[str, ...] = tuple(name for name in STEP_NAMES if name != "rate-limit")

RATE_LIMIT_DEFAULT_BURST = 5
#: Hard cap enforced in code (kb/proposals/capability-audit.md, NT-1): a probe,
#: not a load test.
RATE_LIMIT_HARD_CAP = 20

#: Header names worth recording on a rate-limit probe call, even on a 200.
_RATE_LIMIT_HEADER_PREFIXES = ("x-ratelimit", "ratelimit")


@dataclass
class Finding:
    """One step's outcome: whether it worked, and what it found."""

    name: str
    ok: bool
    endpoint: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "endpoint": self.endpoint,
            "summary": self.summary,
            "evidence": self.evidence,
            "error": self.error,
        }


#: Printed and exported verbatim — the hard design rule from the audit: this
#: tool produces a report for human fold-in, it never writes the KB itself.
FOLD_IN_NOTE = "findings are for human fold-in to kb/trackspace.json; this tool never writes the KB"


@dataclass
class FindingsReport:
    """The whole run: when it happened, and every step's finding."""

    probed_at: str
    findings: list[Finding]
    note: str = FOLD_IN_NOTE

    @property
    def ok(self) -> bool:
        return all(finding.ok for finding in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "probed_at": self.probed_at,
            "note": self.note,
            "findings": [finding.to_dict() for finding in self.findings],
        }


def assert_get_only(kb: KnowledgeBase) -> None:
    """Refuse to run if any endpoint a probe step uses is not a GET.

    This tool is read-only by design (NT-1). Checking the knowledge base at
    startup — rather than trusting the step code to keep behaving — means a
    doctored or mis-edited KB entry cannot turn a probe into a mutation.
    """
    for step_name, endpoint_names in STEP_ENDPOINTS.items():
        for endpoint_name in endpoint_names:
            spec = kb.endpoint(endpoint_name)
            if spec.method != "GET":
                raise ConfigurationError(
                    f"instance probe is read-only but step {step_name!r} uses endpoint "
                    f"{endpoint_name!r} which is {spec.method}, not GET — refusing to run"
                )


def _issue_and_project_key(kb: KnowledgeBase) -> tuple[str, str]:
    """The CLOPSSEC issue/project keys, from the KB default — never hardcoded."""
    issue_key = cast(str, kb.default("issue_key"))
    return issue_key, issue_key.split("-")[0]


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
def _step_fields(client: TrackspaceClient) -> Finding:
    endpoint = "field_list"
    try:
        data = client.request_json(endpoint)
    except TrackspaceError as exc:
        return Finding("fields", False, endpoint, f"GET {endpoint} failed: {exc}", {}, str(exc))
    if not isinstance(data, list):
        return Finding(
            "fields",
            False,
            endpoint,
            f"unexpected response shape from {endpoint}: expected a list",
            {"raw": data},
        )
    fields = cast(list[dict[str, Any]], data)
    system_fields = [f for f in fields if not f.get("custom")]
    custom_fields = [f for f in fields if f.get("custom")]
    custom_summary = [
        {"id": f.get("id"), "name": f.get("name"), "clauseNames": f.get("clauseNames", [])}
        for f in custom_fields
    ]
    summary = (
        f"{len(fields)} fields total — {len(system_fields)} system, {len(custom_fields)} custom"
    )
    evidence = {
        "total": len(fields),
        "system_count": len(system_fields),
        "custom_count": len(custom_fields),
        "custom_fields": custom_summary,
    }
    return Finding("fields", True, endpoint, summary, evidence)


def _step_project(client: TrackspaceClient, kb: KnowledgeBase) -> Finding:
    endpoint = "project_get"
    _issue_key, project_key = _issue_and_project_key(kb)
    try:
        data = client.request_json(endpoint, path_params={"project_key": project_key})
    except TrackspaceError as exc:
        return Finding("project", False, endpoint, f"GET {endpoint} failed: {exc}", {}, str(exc))
    if not isinstance(data, dict):
        return Finding(
            "project",
            False,
            endpoint,
            f"unexpected response shape from {endpoint}: expected an object",
            {"raw": data},
        )
    name = data.get("name")
    issue_types = data.get("issueTypes", [])
    summary = f"{project_key} = {name!r} ({len(issue_types)} issue types)"
    evidence = {"key": project_key, "name": name, "issue_types": issue_types}
    return Finding("project", True, endpoint, summary, evidence)


def _step_createmeta(client: TrackspaceClient, kb: KnowledgeBase) -> Finding:
    endpoint = "issue_createmeta"
    _issue_key, project_key = _issue_and_project_key(kb)
    try:
        data = client.request_json(
            endpoint,
            params={"projectKeys": project_key, "expand": "projects.issuetypes.fields"},
        )
    except TrackspaceError as exc:
        return Finding("createmeta", False, endpoint, f"GET {endpoint} failed: {exc}", {}, str(exc))
    if not isinstance(data, dict):
        return Finding(
            "createmeta",
            False,
            endpoint,
            f"unexpected response shape from {endpoint}: expected an object",
            {"raw": data},
        )
    projects = data.get("projects", [])
    issuetypes = projects[0].get("issuetypes", []) if projects else []
    summary = f"{len(issuetypes)} creatable issue type(s) for {project_key}"
    evidence = {"project_key": project_key, "projects": projects}
    return Finding("createmeta", True, endpoint, summary, evidence)


def _step_statuses(client: TrackspaceClient) -> Finding:
    endpoint = "status_list"
    try:
        data = client.request_json(endpoint)
    except TrackspaceError as exc:
        return Finding("statuses", False, endpoint, f"GET {endpoint} failed: {exc}", {}, str(exc))
    if not isinstance(data, list):
        return Finding(
            "statuses",
            False,
            endpoint,
            f"unexpected response shape from {endpoint}: expected a list",
            {"raw": data},
        )
    names = [status.get("name") for status in data]
    summary = f"{len(names)} status(es): {', '.join(str(name) for name in names)}"
    evidence = {"statuses": data}
    return Finding("statuses", True, endpoint, summary, evidence)


def _step_transitions(client: TrackspaceClient, kb: KnowledgeBase) -> Finding:
    endpoint = "issue_transitions"
    issue_key, _project_key = _issue_and_project_key(kb)
    try:
        data = client.request_json(
            endpoint,
            path_params={"issue_key": issue_key},
            params={"expand": "transitions.fields"},
        )
    except TrackspaceError as exc:
        return Finding(
            "transitions", False, endpoint, f"GET {endpoint} failed: {exc}", {}, str(exc)
        )
    if not isinstance(data, dict):
        return Finding(
            "transitions",
            False,
            endpoint,
            f"unexpected response shape from {endpoint}: expected an object",
            {"raw": data},
        )
    transitions = data.get("transitions", [])
    names = ", ".join(str(t.get("name")) for t in transitions) or "none"
    summary = f"{len(transitions)} transition(s) from {issue_key}'s current status: {names}"
    evidence = {"issue_key": issue_key, "transitions": transitions}
    return Finding("transitions", True, endpoint, summary, evidence)


def _step_permissions(client: TrackspaceClient, kb: KnowledgeBase) -> Finding:
    endpoint = "my_permissions"
    _issue_key, project_key = _issue_and_project_key(kb)
    try:
        data = client.request_json(endpoint, params={"projectKey": project_key})
    except TrackspaceError as exc:
        return Finding(
            "permissions", False, endpoint, f"GET {endpoint} failed: {exc}", {}, str(exc)
        )
    if not isinstance(data, dict):
        return Finding(
            "permissions",
            False,
            endpoint,
            f"unexpected response shape from {endpoint}: expected an object",
            {"raw": data},
        )
    permissions = cast(dict[str, dict[str, Any]], data.get("permissions", {}))
    granted = sorted(key for key, spec in permissions.items() if spec.get("havePermission"))
    denied = sorted(key for key, spec in permissions.items() if not spec.get("havePermission"))
    summary = (
        f"{len(granted)} granted, {len(denied)} denied for {project_key} "
        f"(denied: {', '.join(denied) or 'none'})"
    )
    evidence = {"project_key": project_key, "permissions": permissions}
    return Finding("permissions", True, endpoint, summary, evidence)


def _probe_issue_get(client: TrackspaceClient, issue_key: str) -> dict[str, Any]:
    """One GET against a (deliberately) bad issue key, status/body captured raw."""
    try:
        client.request_json("issue_get", path_params={"issue_key": issue_key})
    except ApiError as exc:
        return {"status": exc.status, "body": exc.body, "error": None}
    except TrackspaceError as exc:
        return {"status": None, "body": None, "error": str(exc)}
    return {"status": 200, "body": None, "error": None}


def _step_error_shape(client: TrackspaceClient) -> Finding:
    endpoint = "issue_get"
    # "BOGUS-1" (syntactically valid, doesn't exist) and "not-a-key" (malformed)
    # are probe inputs chosen to exercise the error path — not instance facts,
    # so they come from the step itself, not the knowledge base.
    bogus = _probe_issue_get(client, "BOGUS-1")
    malformed = _probe_issue_get(client, "not-a-key")
    evidence = {"bogus_key": bogus, "malformed_key": malformed}

    if bogus["status"] == 404:
        summary = (
            f"BOGUS-1 correctly 404s (body captured); not-a-key returned {malformed['status']}"
        )
        return Finding("error-shape", True, endpoint, summary, evidence)
    if bogus["error"]:
        return Finding(
            "error-shape",
            False,
            endpoint,
            f"BOGUS-1 raised an unexpected error: {bogus['error']}",
            evidence,
            cast(str, bogus["error"]),
        )
    return Finding(
        "error-shape",
        False,
        endpoint,
        f"BOGUS-1 returned {bogus['status']}, expected 404",
        evidence,
    )


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def _is_rate_limit_header(name: str) -> bool:
    low = name.lower()
    return low == "retry-after" or low.startswith(_RATE_LIMIT_HEADER_PREFIXES)


def _step_rate_limit(client: TrackspaceClient, kb: KnowledgeBase, burst: int) -> Finding:
    endpoint = "myself"
    if not 1 <= burst <= RATE_LIMIT_HARD_CAP:
        raise ConfigurationError(
            f"--rate-limit-burst must be between 1 and the hard cap of "
            f"{RATE_LIMIT_HARD_CAP} (got {burst})"
        )
    spec = kb.endpoint(endpoint)
    url = f"{client.base_url}{client.api_root}{spec.render(client.api_root)}"

    calls: list[dict[str, Any]] = []
    too_many_requests = 0
    for _ in range(burst):
        started = time.monotonic()
        try:
            # Deliberately below request_json: this needs the raw response
            # (status + headers, even on 200) and retry=False, which a single
            # unretried session.request call gives for free.
            response = client.session.request(
                "GET", url, params=None, json=None, timeout=spec.timeout_s
            )
        except requests.RequestException as exc:
            calls.append(
                {
                    "status": None,
                    "elapsed_ms": _elapsed_ms(started),
                    "headers": {},
                    "error": str(exc),
                }
            )
            continue
        status = response.status_code
        if status == 429:
            too_many_requests += 1
        headers = {k: v for k, v in response.headers.items() if _is_rate_limit_header(k)}
        calls.append(
            {
                "status": status,
                "elapsed_ms": _elapsed_ms(started),
                "headers": headers,
                "error": None,
            }
        )

    probed_date = datetime.now(UTC).date().isoformat()
    # Deliberately honest wording (KBW-6): never "no rate limit" / "confirmed".
    summary = f"{too_many_requests}/{burst} requests returned 429 at burst={burst} on {probed_date}"
    evidence = {"burst": burst, "calls": calls}
    return Finding("rate-limit", True, endpoint, summary, evidence)


_StepFn = Callable[[TrackspaceClient, KnowledgeBase, int | None], Finding]


def _run_step(
    name: str, client: TrackspaceClient, kb: KnowledgeBase, *, rate_limit_burst: int | None
) -> Finding:
    if name == "fields":
        return _step_fields(client)
    if name == "project":
        return _step_project(client, kb)
    if name == "createmeta":
        return _step_createmeta(client, kb)
    if name == "statuses":
        return _step_statuses(client)
    if name == "transitions":
        return _step_transitions(client, kb)
    if name == "permissions":
        return _step_permissions(client, kb)
    if name == "error-shape":
        return _step_error_shape(client)
    if name == "rate-limit":
        burst = rate_limit_burst if rate_limit_burst is not None else RATE_LIMIT_DEFAULT_BURST
        return _step_rate_limit(client, kb, burst)
    raise ConfigurationError(f"unknown probe step {name!r}; choices are {STEP_NAMES}")


def run_probe(
    client: TrackspaceClient,
    kb: KnowledgeBase,
    *,
    steps: Sequence[str] = DEFAULT_STEPS,
    rate_limit_burst: int | None = None,
    on_step: Callable[[str, Finding], None] | None = None,
) -> FindingsReport:
    """Run each named step in order, collecting a :class:`Finding` from each.

    A step that raises :class:`~trackspace.errors.TrackspaceError` while
    calling Trackspace is captured as a failed finding by the step itself —
    this function never needs to catch that to keep the run going. A
    :class:`~trackspace.errors.ConfigurationError` raised for a genuinely bad
    *invocation* (e.g. an over-cap rate-limit burst) is not swallowed: it
    propagates, because that is a reason to stop, not a per-step finding.
    """
    findings: list[Finding] = []
    for name in steps:
        finding = _run_step(name, client, kb, rate_limit_burst=rate_limit_burst)
        findings.append(finding)
        if on_step is not None:
            on_step(name, finding)
    return FindingsReport(probed_at=datetime.now(UTC).isoformat(), findings=findings)
