# Instance, auth and endpoints

## Instance

| Fact | Value | Source |
| --- | --- | --- |
| Base URL | `https://trackspace.lhsystems.com` | `work_log.py:127`, `work.py:694` |
| API root | `/rest/api/2` | `work_log.py:240,253,279,307`, `work.py:262,699` |
| Product | Jira Data Center / Server | inferred |
| PAT self-service page | `https://trackspace.lhsystems.com/secure/ViewProfile.jspa` | `work_log.py:30-31` |
| Default timezone for booked time | `Europe/Berlin` | `work_log.py:128` |

The product identification is inferred, but from four independent signals: Bearer
PAT auth, the `/rest/api/2` root, users identified by `name`/`key` rather than
`accountId`, and a `ViewProfile.jspa` PAT page. All four are Data Center, none is
Cloud.

`work.py`'s docstring calls the host `lhsystems.int` while every URL in both
scripts is `trackspace.lhsystems.com`. The URLs win; the docstring is prose.

## Auth

```
Authorization: Bearer <TRACKSPACE_PAT>
Accept: application/json
Content-Type: application/json      # sent by the scheduler; the visualiser omits it on GETs
```

The token comes from the `TRACKSPACE_PAT` environment variable, which is present
in every environment this code runs in. It is never prompted for, never written to
disk, and never logged — tools report auth as *present* or *missing* only.

`work.py` also reads `JIRA_API_TOKEN` as a fallback and supports HTTP basic auth
via `JIRA_AUTH_TYPE=basic` plus `JIRA_EMAIL`. That path exists in the code but has
no evidence of ever being used against this instance; Data Center PATs are Bearer
tokens.

### Environment overrides

| Variable | Effect | Source |
| --- | --- | --- |
| `TRACKSPACE_PAT` | The PAT. Required. | `work_log.py:791`, `work.py:697` |
| `JIRA_API_TOKEN` | Fallback token, visualiser only. | `work.py:697` |
| `JIRA_BASE_URL` | Overrides the base URL. | `work.py:694` |
| `JIRA_AUTH_TYPE` | `bearer` (default) or `basic`. | `work.py:698` |
| `JIRA_EMAIL` | Username, basic auth only. | `work.py:695` |
| `JIRA_API_VERSION` | API version segment, default `2`. | `work.py:699` |

## Endpoints in use

### `GET /rest/api/2/myself`

Returns the token owner. Consumed keys: `name`, `key`, `displayName`,
`emailAddress`. Timeout 30 s. Fixture: `fixtures/myself.json`.

### `GET /rest/api/2/search`

Params: `jql`, `fields` (comma-separated), `startAt`, `maxResults` (100).
Returns `{startAt, maxResults, total, issues: [{key, fields: {summary}}]}`.
Timeout 60 s in the scheduler, 30 s in the visualiser.
Fixtures: `fixtures/search_worklog_authors_page1.json`, `…_page2.json`,
`fixtures/search_empty.json`.

Only `summary` is ever requested. The call answers "which issues carry my
worklogs", not "what are my worklogs".

### `GET /rest/api/2/issue/{key}/worklog`

Returns `{startAt, maxResults, total, worklogs: [...]}` — **every** author's
worklogs on that issue.

* The scheduler sends no parameters and reads the first page only.
* The visualiser pages with `startAt`/`maxResults=1000` and passes
  `startedAfter=<UTC epoch milliseconds>` as a server-side lower bound.

Timeout 60 s / 30 s respectively. Fixtures:
`fixtures/issue_worklog_CLOPSSEC-41456.json`,
`fixtures/issue_worklog_paged_page1.json`, `…_page2.json`.

### `POST /rest/api/2/issue/{key}/worklog`

```json
{"timeSpentSeconds": 1800, "started": "2026-04-01T10:00:00.000+0200", "comment": "Daily"}
```

Success is `200` **or** `201`; only `id` is read from the response. `comment` is a
plain string — this is API v2, not Cloud's Atlassian Document Format.

**Not idempotent.** There is no dedupe check anywhere: posting the same schedule
twice creates two sets of worklogs. This is why the shared client never retries a
worklog POST automatically. Timeout 30 s.
Fixture: `fixtures/add_worklog_created.json`.

### `GET /rest/api/2/user/search`

Params: `username` (matches username, email or partial display name),
`maxResults=2`. Returns a JSON **array** on Data Center; the visualiser also
tolerates a `{"values": [...]}` envelope for Cloud. First match wins.
Fixtures: `fixtures/user_search.json`, `fixtures/user_search_empty.json`.
