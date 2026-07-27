# Trackspace knowledge base

Everything this repo knows about the Trackspace instance, extracted from the two
scripts that are proven against production. Tools read the facts from
[`trackspace.json`](trackspace.json) — the Markdown here is the human-readable
companion, not a second source of truth.

Trackspace is the internal name for the instance. "Jira" appears only when naming
the underlying REST API.

| File | Contents |
| --- | --- |
| [`trackspace.json`](trackspace.json) | Machine-readable instance, auth, endpoint, field, JQL, pagination and error maps. Imported via `trackspace.kb`. |
| [`instance.md`](instance.md) | Base URL, API version, auth scheme, endpoint catalogue. |
| [`fields.md`](fields.md) | Field ids → names → what they actually hold. Projects, issue types, workflow states. |
| [`jql.md`](jql.md) | JQL patterns known to work here, and their limits. |
| [`pagination-and-errors.md`](pagination-and-errors.md) | Pagination shape, error responses, timeouts, retry policy. |
| [`quirks.md`](quirks.md) | Everything the working scripts do that looks odd, and why it is deliberate. |
| [`fixtures/`](fixtures/) | Offline JSON samples for every endpoint. The test suite runs entirely against these. |

## Provenance

Every fact carries a `source`, in one of two forms:

* `work_log.py:240` — a line in one of the two original scripts.
* `inferred` — reasoned from the code or from how Jira Data Center behaves in
  general, but not directly witnessed. Treat these as working assumptions.

The two source scripts have since been renamed and moved:

| Original | Now |
| --- | --- |
| `work_log.py` | [`worklog_scheduler/schedule_and_post_worklogs.py`](../worklog_scheduler/schedule_and_post_worklogs.py) |
| `work.py` (self-titled `visualize_jira_worklogs.py`) | [`worklog_visualizer/visualize_logged_worklogs.py`](../worklog_visualizer/visualize_logged_worklogs.py) |

Line references in `source` fields point at the **originals as delivered** — the
two standalone scripts this repo was built from. They were never committed here,
so the line numbers are a frozen record of the evidence rather than something you
can click; they stay valid because they do not move when the rewritten code does.

## What is not in here

Recorded as unknown rather than guessed:

* **Custom field ids.** Neither script references a `customfield_*` id. The map is
  empty because nothing was observed, not because the instance has none.
* **Issue types and workflow states.** Never read, filtered on or transitioned to.
* **Rate-limit behaviour.** No 429 was ever observed or handled.
* **The `CLOPSSEC` project's human-readable name.** Only the key appears.
* **Error body shape.** Assumed to be the standard Data Center
  `{"errorMessages": [...], "errors": {...}}`; only the fact that the raw body is
  sliced to 200 characters is confirmed.
