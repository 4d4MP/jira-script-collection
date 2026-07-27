# Offline fixtures

Synthetic responses covering every endpoint either tool calls. The test suite runs
entirely against these — no network, no PAT.

**All content is synthetic.** Shapes, key names, timestamp formats, pagination
fields and error envelopes are reproduced faithfully from what the two proven
scripts read and write; users, issue summaries and ids are invented. Emails use
`example.invalid`.

| File | Endpoint | Exercises |
| --- | --- | --- |
| `myself.json` | `GET /myself` | Identity keys `name`, `key`, `displayName`, `emailAddress` |
| `user_search.json` | `GET /user/search` | Data Center array response; "first match wins" |
| `user_search_empty.json` | `GET /user/search` | No user found |
| `search_worklog_authors_page1.json` | `GET /search` | `total=5`, `maxResults=3` — a **short page**, so a pager that advances by requested size would skip rows |
| `search_worklog_authors_page2.json` | `GET /search` | Second and final page |
| `search_empty.json` | `GET /search` | Empty result set |
| `issue_worklog_CLOPSSEC-41456.json` | `GET /issue/{key}/worklog` | Foreign author, out-of-range entry, `Z`-suffixed `started`, missing `comment` |
| `issue_worklog_CLOPSSEC-41501.json` | `GET /issue/{key}/worklog` | Three entries — the test server serves them two at a time to exercise worklog pagination |
| `issue_worklog_CLOPSSEC-41502/41677/41703.json` | `GET /issue/{key}/worklog` | The rest of the search result set; 41677 has a same-slot entry by another author |
| `issue_worklog_empty.json` | `GET /issue/{key}/worklog` | Issue with no worklogs |
| `issue_worklog_malformed.json` | `GET /issue/{key}/worklog` | Missing `started`, unparseable `started`, empty `author`, missing `timeSpentSeconds`, ADF `comment` object |
| `add_worklog_created.json` | `POST /issue/{key}/worklog` | 201 response; only `id` is consumed |
| `errors.json` | any | 401 / 403 / 404 / 429 (+`Retry-After`) / 500 / 400-on-worklog bodies |

## The scenario the fixtures describe

Token owner `adam.papp` (`JIRAUSER10042`, "Adam Papp"), window
**2026-04-01 → 2026-04-30**, five issues in `CLOPSSEC`. Their summaries include a
bare IPv4, an IPv4 with a port, and an IPv6 address, so both tools' title
normalisers have something to collapse.

Worklogs the token owner actually owns inside the window total **9.25 h** across
five issues (2.5 + 2.5 + 1.0 + 2.0 + 1.25); the fixtures also contain time that
must be filtered out — Jane Doe's entries and one entry on 2026-03-30.
