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
| `field_list.json` | `GET /field` | System fields plus 3 custom fields (select/float/option) — **placeholder until probed** |
| `project_CLOPSSEC.json` | `GET /project/{key}` | Project name, lead, issue types — **placeholder until probed** |
| `issue_createmeta_CLOPSSEC.json` | `GET /issue/createmeta` | Issue types/fields creatable in `CLOPSSEC` — **placeholder until probed** |
| `status_list.json` | `GET /status` | Global status catalogue (To Do/In Progress/Done) — **placeholder until probed** |
| `issue_transitions_CLOPSSEC-41456.json` | `GET /issue/{key}/transitions` | Two available transitions — **placeholder until probed** |
| `issue_get_CLOPSSEC-41456.json` | `GET /issue/{key}` | Full issue with `?expand=changelog` history and `?fields=attachment` list |
| `mypermissions_CLOPSSEC.json` | `GET /mypermissions` | Mixed granted/denied permissions (`ADMINISTER` denied) — **placeholder until probed** |
| `comment_list_CLOPSSEC-41456.json` | `GET /issue/{key}/comment` | Three comments, two authors, offset envelope |
| `comment_created.json` | `POST`/`PUT /issue/{key}/comment[/{id}]` | Single created/updated comment object |
| `attachment_get.json` | `GET /attachment/{id}` | Single attachment's metadata |
| `attachment_created.json` | `POST /issue/{key}/attachments` | Upload response — a JSON array with one attachment |
| `attachment_meta.json` | `GET /attachment/meta` | `{enabled, uploadLimit}` |
| `issue_link_get.json` | `GET /issueLink/{id}` | One link with its inward/outward issues |
| `issue_link_type_list.json` | `GET /issueLinkType` | Blocks/Cloners/Duplicate/Relates |
| `remote_link_list_CLOPSSEC-41456.json` | `GET /issue/{key}/remotelink` | Two remote links (PR, runbook) |
| `remote_link_created.json` | `POST /issue/{key}/remotelink` | `{id, self}` creation response |
| `no_content.json` | `POST`/`PUT`/`DELETE` (204/no-body responses) | Empty `{}` body for transition-execute, comment-delete, attachment-delete, issue-link create/delete, remote-link delete |

## The scenario the fixtures describe

Token owner `adam.papp` (`JIRAUSER10042`, "Adam Papp"), window
**2026-04-01 → 2026-04-30**, five issues in `CLOPSSEC`. Their summaries include a
bare IPv4, an IPv4 with a port, and an IPv6 address, so both tools' title
normalisers have something to collapse.

Worklogs the token owner actually owns inside the window total **9.25 h** across
five issues (2.5 + 2.5 + 1.0 + 2.0 + 1.25); the fixtures also contain time that
must be filtered out — Jane Doe's entries and one entry on 2026-03-30.
