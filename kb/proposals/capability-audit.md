# Capability audit: what the Trackspace platform could do and doesn't

A discovery pass over `trackspace/` (the shared client, knowledge base and CLI kit) and its two tools, `worklog_scheduler` and `worklog_visualizer`, which together reach five endpoints (`GET /myself`, `GET /search`, `GET`/`POST /issue/{key}/worklog`, `GET /user/search`) — all worklog time-tracking. This catalogues what else the platform could support: new tools, additive features in the two existing tools, shared-library capability, KB work, and hygiene. Nothing here has been implemented; this file is the only change.

**How to read an entry.** Every entry states: what it depends on, its KB status (already known / `inferred` / a declared KB unknown — see `kb/README.md` "What is not in here" and `kb/trackspace.json`'s `*_note` fields), its frozen-behaviour impact (additive, or the exact pinned assertion it would touch — see `CLAUDE.md`'s "Tests" section), what fixtures it needs and what only production could confirm, its evidence, size, dependencies, confidence, and value-for-cost. Confidence is about whether the capability works as described on *this* instance, not whether the idea is good.

Fourteen background research passes fed this (three orienting reads of the docs/library/tools, ten fanned out across the request's axes). Findings that surfaced from more than one pass are merged into a single entry citing the strongest evidence.

---

## Cross-cutting facts, established once here

- **Jira Data Center tops out at `/rest/api/2`.** There is no `/rest/api/3` on Data Center (Cloud-only, added for Atlassian Document Format) — so ADF, Cloud-style bulk-operations (`/rest/api/3/bulk/issues/...`), and Cloud's mandatory `permissions=` param on `/mypermissions` do not apply here. Confidence: high (corroborated across three research passes); still `unverified` in the sense that none of it has been called against `trackspace.lhsystems.com` itself.
- **No dedicated changelog endpoint exists on Data Center** (Cloud-only) — history comes back embedded via `?expand=changelog` on `/issue/{key}` or `/search`, not a standalone route.
- **No dedicated "list attachments" endpoint exists** — the list comes from `GET /issue/{key}?fields=attachment`.
- **No bulk worklog endpoint exists anywhere** in core Jira v2, on any platform. Bulk issue *creation* exists on Data Center (`POST /issue/bulk`); bulk *transition/edit/move* (`/rest/api/3/bulk/issues/...`) is Cloud-only and has no Data Center equivalent — Data Center's bulk-change UI is an internal servlet, not documented public REST.
- **Boards/sprints (`/rest/agile/1.0/...`) require a Jira Software license**, a different product tier from core Jira. Nothing in this repo's KB confirms Trackspace has it. Any sprint-aware idea below is gated on a single defensive `GET /rest/agile/1.0/board` call resolving instead of 404ing.
- **`issueFunction()` and similar extended JQL functions require the ScriptRunner plugin** (or equivalent) — not core Jira on either platform. Do not depend on it without confirming the plugin.
- **A real correctness gap, not a proposal:** neither existing JQL template (`worklogs_by_current_user_in_range`, `worklogs_by_named_user_in_range`) carries an `ORDER BY`. Both tools page by offset (`startAt`/`maxResults`). Without a stable sort, a search racing concurrent issue edits could skip or duplicate rows across pages. Adding `ORDER BY key ASC` closes this — see KBW-8.
- **WebFetch against `developer.atlassian.com` returned 403 for the entire session** (confirmed by a control fetch against a neutral domain also failing) — the API-surface findings below (axes 1a/1b/1c) rest on WebSearch result snippets that quote or cite the official docs, not rendered pages. Treat versioned doc-slug URLs as best-effort locators. Where a specific claim rests on a search snippet alone rather than corroborated detail, it's marked `unverified` inline.

---

## New tools

### NT-1 — A read-only "instance probe" that turns the KB's declared unknowns into sourced facts
**Pitch:** one GET-only diagnostic run that resolves custom field ids, issue types, workflow states, the CLOPSSEC project's real name, the actual error body shape, and (carefully) rate-limit behaviour — the six facts `kb/trackspace.json` currently marks unknown or inferred.
**Endpoints/fields:** `GET /rest/api/2/field` (all fields incl. `customfield_*`), `GET /rest/api/2/project/{key}` (name + issue types), `GET /rest/api/2/issue/createmeta` (issue types scoped to what can actually be created), `GET /rest/api/2/status` (global status catalogue), `GET /rest/api/2/issue/{key}/transitions` (read-only listing, not execution), `GET /rest/api/2/issue/{key}` against a bogus key (captures the real error body), `GET /rest/api/2/mypermissions` (explains 403s). None of these are in `kb/trackspace.json` today — all seven would be new KB endpoint entries.
**KB status:** every fact this probe produces is a declared unknown today (`fields.custom = {}`, `fields.issue_types_note`, `fields.workflow_states_note`, `projects.CLOPSSEC.name = "UNKNOWN"`, `errors.body_shape.source = "inferred"`, `errors.rate_limiting.source = "inferred"` — all confirmed verbatim against the live file). This tool's entire purpose is to move them from unknown/inferred to sourced.
**Frozen-behaviour impact:** none — a new, separate package; touches nothing in the two existing tools.
**Fixtures needed:** one new fixture per new endpoint (`field_list.json`, `project_CLOPSSEC.json`, `issue_createmeta_CLOPSSEC.json`, `status_list.json`, `issue_transitions_CLOPSSEC-41456.json`, `issue_get_not_found.json`, `mypermissions_CLOPSSEC.json`), each documented in `kb/fixtures/README.md` and routed in `tests/conftest.py`. What only production can confirm: literally everything — the whole point is these fixtures are currently invented placeholders until a real probe run captures the actual shapes.
**Design decision worth keeping:** the probe should emit a findings report for human fold-in, not self-edit `kb/trackspace.json` — preserves the KB's existing discipline that every fact's `source` is a reviewable citation, not a silently machine-written value. The rate-limit probe specifically should stay a small, hard-capped burst (e.g. 5, capped at 20) against the cheapest existing endpoint (`/myself`), with `retry=False` so a real 429 is observed raw rather than absorbed by the client's own backoff — and the result should be framed honestly as "0/N 429s at burst=N on `<date>`", not upgraded to "confirmed no rate limit."
**Evidence:** `trackspace/kb.py` (mechanism), `kb/trackspace.json` (six unknowns, verified live), axis-2 research pass (endpoint selection + gating map).
**Size:** medium (new package, 7 endpoints, 7 fixtures, one findings-report format).
**Dependencies:** none. Gates: KBW-1 through KBW-7, LIB-1, NT-2/NT-3 (need issue types/workflow states first to be safe), most of the "field-metadata-aware" ideas below.
**Confidence:** high that the mechanism works (all seven endpoints are core-Jira, DC-v2-confirmed by two independent research passes). Low-to-medium confidence in what it will *find* — genuinely unknown until run.
**Value:** high, low cost. This is the one recommendation that unblocks the largest number of others; see the shortlist.

### NT-2 — Workflow transitions tool
**Pitch:** list and (carefully, opt-in) execute the transitions available on an issue, so a script can move a ticket instead of only logging time against it.
**Endpoints/fields:** `GET /issue/{key}/transitions[?expand=transitions.fields]` (list, confirmed DC v2), `POST /issue/{key}/transitions` (execute, confirmed DC v2, body `{"transition":{"id":"..."},"update":{"comment":[...]}}`).
**KB status:** workflow states are a declared unknown (`fields.workflow_states_note`). This tool cannot be built safely until NT-1/KBW-3 records at least one real transition graph — guessing a transition id and POSTing it is exactly the kind of invention the KB's provenance rule forbids.
**Frozen-behaviour impact:** none — new tool, no existing code touched.
**Fixtures needed:** `issue_transitions_CLOPSSEC-41456.json` (shared with NT-1), plus a POST-response fixture (204 No Content, per docs).
**Evidence:** axis-1a research pass, corroborated by `developer.atlassian.com/server/jira/platform/rest/v10000/api-group-issue/` and the classic `IssueResource` javadoc.
**Size:** medium (list is small; safe execute needs a discovery-then-confirm UX, not a blind POST).
**Dependencies:** hard-gated on NT-1/KBW-3.
**Confidence:** high the endpoints exist and work as documented; low confidence on what transitions/permissions this PAT actually has until probed.
**Value:** medium — real capability, but the scheduler/visualiser have no current workflow-adjacent need; this is closer to "the platform could" than "a tool wants this now."

### NT-3 — Comment tool (read, post, and idempotent status-comment upsert)
**Pitch:** read an issue's comments and post/update one — including an auto-generated "logged N hours via Trackspace" comment as a natural companion to `add_worklog`.
**Endpoints/fields:** `GET /issue/{key}/comment` (list, same offset envelope the client's `_page_of` already parses), `POST /issue/{key}/comment` (body `{"body": "plain text"}` — v2 plain string, matching the KB's existing worklog-comment convention, not ADF), `GET/PUT/DELETE /issue/{key}/comment/{id}` (single-comment ops). All confirmed DC v2.
**KB status:** none of these facts are unknown or inferred — comment body format on v2 is already established by the worklog-comment precedent in `kb/quirks.md` #2 (plain string, not ADF). No probe dependency.
**Frozen-behaviour impact:** none directly. If wired as "auto-comment after a live submit" inside the scheduler, it would extend (not change) the existing post-submit flow — additive as long as it's opt-in and doesn't alter `"Posted N/M worklogs to X"` or any pinned string.
**Fixtures needed:** `comment_list_CLOPSSEC-41456.json`, `comment_created.json`. Nothing here needs production to confirm beyond the general "does POST /comment actually accept a plain-string body on this instance" — high confidence given the identical convention already proven for worklog comments.
**Evidence:** axis-1a research pass; `CommentResource` javadoc; `developer.atlassian.com/server/jira/platform/jira-rest-api-example-add-comment-8946422/`.
**Size:** small (read) to medium (upsert-with-visibility logic).
**Dependencies:** none required; pairs naturally with SC-20 (scheduler auto-comment).
**Confidence:** high.
**Value:** medium-high — cheap to build, immediately useful as a companion to the scheduler's existing posting flow.

### NT-4 — Attachment tool (upload generated reports, list, download)
**Pitch:** attach a generated report (the visualiser's PNG/CSV export) directly to a Trackspace issue instead of moving it by hand.
**Endpoints/fields:** `POST /issue/{key}/attachments` (multipart upload, requires header `X-Atlassian-Token: no-check` — confirmed DC v2), `GET /issue/{key}?fields=attachment` (list — there is no dedicated list-attachments route on DC), `GET /attachment/{id}` (metadata — `unverified`, exact field names not directly rendered this session), `DELETE /attachment/{id}` (confirmed), `GET /attachment/content/{id}` (download, confirmed route, byte-range support `unverified`), `GET /attachment/meta` (`{enabled, uploadLimit}` — `unverified`, worth a cheap live GET before relying on it).
**KB status:** no unknowns block this — it's genuinely untouched surface, not gated on a probe.
**Frozen-behaviour impact:** none — new capability. If wired into the visualiser's `--export` flow, it would be a new opt-in step after the existing (unchanged) export, not a replacement.
**Fixtures needed:** `attachment_meta.json`, `issue_get_with_attachments.json`, `attachment_created.json`. What only production can confirm: the exact field names on `GET /attachment/{id}` and whether `/attachment/meta` is present un-deprecated on this DC version — flagged `unverified` above precisely because WebFetch was down this session.
**Evidence:** axis-1a research pass; `developer.atlassian.com/server/jira/platform/rest/v10002/api-group-attachment/`; Atlassian Support KB on multipart upload.
**Size:** medium.
**Dependencies:** pairs well with VZ-8/VZ-16 (an export worth attaching).
**Confidence:** high on upload/delete/download routes; medium on the two `unverified` metadata endpoints.
**Value:** medium — nice-to-have, not blocking anything else.

### NT-5 — Issue link / remote link tool
**Pitch:** attach a clickable trail (a PR, a runbook, a dashboard) to the issue a worklog was booked against, and read/manage issue-to-issue relationships.
**Endpoints/fields:** `POST/GET/DELETE /issueLink[/{id}]` (confirmed DC v2), `GET /issueLinkType` (confirmed — needed to know valid `type.name` values before linking), `GET/POST/PUT/DELETE /issue/{key}/remotelink[/{id}]` (confirmed DC v2; `POST` supports an idempotent `globalId` so re-running doesn't duplicate the link).
**KB status:** no unknowns block this.
**Frozen-behaviour impact:** none — new tool. If wired as "attach a remote link when posting a worklog," it's a new opt-in field on the scheduler's meeting spec, additive.
**Fixtures needed:** `issue_link_type_list.json`, `remote_link_created.json`, `remote_link_list_CLOPSSEC-41456.json`.
**Evidence:** axis-1b research pass; `developer.atlassian.com/server/jira/platform/rest/v10002/api-group-issuelink/` and the remote-issue-links doc family.
**Size:** small-medium.
**Dependencies:** none.
**Confidence:** high.
**Value:** medium — the `globalId` idempotency detail specifically makes this safer than most write-capable ideas here, worth noting.

### NT-6 — Issue changelog / audit-trail tool
**Pitch:** reconstruct who changed what on an issue and when — status flips, field edits, assignee changes — for compliance or "why did this ticket's state change" questions.
**Endpoints/fields:** `GET /issue/{key}?expand=changelog` (embeds `changelog.histories[]`, confirmed DC v2 — **there is no dedicated changelog route on Data Center**, Cloud-only), `GET /search?jql=...&expand=changelog` (same expansion, bulk across a JQL result set, reuses the existing `search` endpoint the client already calls).
**KB status:** no unknowns block this.
**Frozen-behaviour impact:** none if built as a new tool. If added as an `expand` parameter option on the *existing* `search` KB endpoint, it's additive (new optional param, default behaviour of `iter_search_issues`/`search_issues` unchanged).
**Fixtures needed:** `issue_get_with_changelog.json`, `search_with_changelog.json`. What only production can confirm: whether `histories[]` is capped per page on this DC version and needs client-side paging beyond what `_page_of` already handles (community sources suggest a cap, e.g. ~100 entries, but this is `unverified` for this instance specifically).
**Evidence:** axis-1a research pass; `developer.atlassian.com/server/jira/platform/rest/v10000/api-group-issue/`; multiple Atlassian Community threads confirming the DC-vs-Cloud changelog-route difference.
**Size:** small (the `expand` param is nearly free to add to the existing `search` KB entry) to medium (a standalone changelog-history renderer).
**Dependencies:** none.
**Confidence:** high on the mechanism; medium on the pagination-cap detail.
**Value:** medium.

### NT-7 — Speculative: webhook-driven live notifier
**Pitch:** instead of the visualiser polling on a schedule, register a Data Center webhook on `jira:worklog_updated` (scoped by JQL) and push events to a small listener — "live" mode instead of pull-based reporting.
**Endpoints/fields:** admin-UI registration, or `POST /rest/webhooks/1.0/webhook` (older DC) / `POST /rest/jira-webhook/1.0/webhooks` (DC 10.0+) — **neither path lives under `/rest/api/2`**, and both require the Jira Administrators global permission, which a normal PAT may not carry. Exact path is version-dependent and `unverified` for this instance.
**KB status:** entirely new surface; not gated on a KB unknown, gated on an admin decision instead.
**Frozen-behaviour impact:** none — a separate long-lived service, not a per-run CLI addition; doesn't fit the existing "each invocation is stateless and short-lived" shape of either tool.
**Fixtures needed:** none meaningful — this can't be fixture-tested the way the rest of the platform is, since it's push-based and admin-provisioned. What only production can confirm: whether this PAT/account has admin rights to register a webhook at all, and which of the two resource paths this DC version uses.
**Evidence:** axis-1c research pass; `developer.atlassian.com/server/jira/platform/webhooks/`; `confluence.atlassian.com/adminjiraserver102/managing-webhooks-1473876247.html`.
**Size:** large (new service, not a CLI feature) — explicitly out of shape for this platform's current architecture.
**Dependencies:** an admin's involvement, outside this repo's control.
**Confidence:** medium the feature exists; low that it fits this platform without a bigger architectural decision.
**Value:** low relative to cost given the current CLI-per-run shape — flagged as speculative and probably not worth pursuing unless the platform's shape changes first.

---

## Features in `worklog_scheduler`

All entries below were checked against the pinned assertions in `tests/test_schedule.py` and `tests/test_scheduler_cli.py` (exact entry-expansion counts/ordering, `_RECURRING_SPEC`/`_ONEOFF_SPEC` regex and error strings, `RecurringMeeting.repeat_str()` exact wording, and CLI output strings like `"Planned worklogs"`, `"DRY RUN"`, `"Posted N/M worklogs to X"`).

### SC-1 — Paginate the scheduler's own worklog dashboard fully
**Pitch:** the scheduler's dashboard currently reads only the **first page** of each issue's worklogs and silently undercounts on heavily-logged issues; the visualiser already paginates fully via the same client method.
**Endpoints/fields:** none new — `client.issue_worklogs(key, paginate=False)` at `worklog_scheduler/dashboard.py:115` would simply drop `paginate=False` (default `True`) and optionally thread `started_after_ms`, matching `worklog_visualizer/fetch.py:143`'s call shape exactly. The client's paginated path already exists and is already tested (`client.py:282-294`).
**KB status:** not KB-gated — this is a client-call-shape change, no new instance fact needed.
**Frozen-behaviour impact:** additive in mechanism, but **user-visible**: totals could legitimately increase for any issue that was silently truncated before. `tests/test_client.py::test_issue_worklogs_unpaginated_sends_no_parameters` pins the *client's* `paginate=False` feature independently (it wouldn't need to change), but no test in `test_dashboard.py` currently asserts a single-request-per-issue call count, so nothing there breaks mechanically — the change is a real behaviour shift worth flagging in a changelog, not a silent bugfix.
**Fixtures needed:** the scheduler's dashboard tests would need a second-page fixture for at least one issue to exercise the new pagination path.
**Evidence:** `worklog_scheduler/dashboard.py:115` (`paginate=False`), `trackspace/client.py:271-272` docstring ("reproduces the scheduler's single unparameterised request"), `kb/quirks.md` #8; axis-3 (quirks) research pass.
**Size:** small (one call-site change) plus test/fixture work.
**Dependencies:** none.
**Confidence:** high — mechanism is already built and proven by the visualiser.
**Value:** high, low cost — the clearest "feature in waiting" in the whole audit.

### SC-2 — Read-only preflight warning before posting a worklog (no dedupe lock)
**Pitch:** before `add_worklog`, GET the issue's existing worklogs for the target window and warn (never block) if the same author already has an entry at the same `started` timestamp — catching a double-run without touching the deliberate no-retry-on-POST policy.
**Endpoints/fields:** reuses `client.issue_worklogs(issue_key, started_after_ms=...)`, already fully paginating. No new endpoint.
**KB status:** not gated — this is a client-side comparison, not a new instance fact.
**Frozen-behaviour impact:** additive if implemented as a warning surfaced through the existing `on_warning` callback pattern (already used in `dashboard.py:118-142`). Must not add any retry or lock to `add_worklog` itself — `client.py:296-316`'s `retry=False` and the "GETs retry, POSTs never do" asymmetry (`client.py:1-9`) stay exactly as documented; this is strictly a new GET before the existing POST, not a change to the POST.
**Fixtures needed:** a fixture with two same-`started` entries from the same author to exercise the warning path.
**Evidence:** `trackspace/client.py:7-9` (no-dedupe docstring), `kb/quirks.md` #12; axis-3 (quirks) research pass.
**Size:** small-medium (one extra GET plus a comparison; risk is false positives from clock skew, so keep it advisory).
**Dependencies:** none.
**Confidence:** high the mechanism works; medium on tuning the match window (±1 minute, comment similarity) without production data.
**Value:** medium-high — addresses a real, named risk (`kb/client.py`'s own docstring calls out double-booking) with a purely additive, low-risk check.

### SC-3 — Read-only "does this issue exist" preflight before a live submit
**Pitch:** fail fast with a clear `ConfigurationError` if the configured issue key doesn't resolve, instead of discovering it via N failed POSTs.
**Endpoints/fields:** reuses `client.search_issues("key = <ISSUE>", ...)` — already exists (`trackspace/client.py:249`), no new endpoint.
**KB status:** not gated.
**Frozen-behaviour impact:** additive to the live path only. `tests/test_scheduler_cli.py::test_dry_run_submit_sends_nothing` asserts dry-run builds no client at all — this check must live strictly in the live branch, after the existing PAT-presence guard (`test_live_submit_without_a_pat_stops_before_any_call` must keep short-circuiting first). The live-submit POST-count assertion (`test_live_submit_posts_every_entry_then_shows_the_dashboard`, filters `call.method == "POST"`) is unaffected since this adds a GET, not a POST.
**Fixtures needed:** none beyond existing search fixtures.
**Evidence:** axis-5a (scheduler features) research pass; `worklog_scheduler/schedule_and_post_worklogs.py` do_submit flow.
**Size:** small.
**Dependencies:** ordering dependency on the existing PAT-check guard, noted above.
**Confidence:** high.
**Value:** medium — cheap safety net.

### SC-4 — Per-meeting issue key override (multi-issue schedules)
**Pitch:** let different recurring/one-off meetings in one schedule target different issues, instead of one `issue_key` for the whole config.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** additive at the data-model level — new optional field defaulting to `""` (meaning "use `cfg.issue_key`", today's sole behaviour). `ScheduleConfig.to_dict()`'s existing key set and the `"jira_base"` naming (pinned by `test_config_round_trip_keeps_the_historical_json_shape`) are untouched since `asdict()` just adds a key both old and new configs tolerate via the existing unknown-key-drop-on-load path. Exact-count assertions (`test_recurring_expansion_and_ordering`, `"7 entries"`/`"3 entries"` in CLI tests) are unaffected for existing single-issue configs, since the default resolves identically.
**Fixtures needed:** none beyond schedule-level test fixtures already present.
**Evidence:** axis-5a research pass.
**Size:** medium-large (touches `config.py`, `schedule.py`'s `WorklogEntry`, and preview/submit rendering to group by issue).
**Dependencies:** none.
**Confidence:** high.
**Value:** medium-high — the most requested-shaped gap (one schedule, one issue) for anyone whose recurring meetings genuinely span multiple tickets.

### SC-5 — Monthly recurrence
**Pitch:** a `MonthlyMeeting` (day-of-month or Nth-weekday-of-month) alongside the existing weekly/every-N-weeks `RecurringMeeting`.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** additive — new top-level `cfg.monthly: list[MonthlyMeeting] = field(default_factory=list)`, defaults to `[]` so old configs (missing the key) are unaffected, and none of the pinned biweekly/weekly `occurs_on` tests are touched since they exercise `RecurringMeeting` only.
**Fixtures needed:** none beyond new unit tests.
**Evidence:** axis-5a research pass.
**Size:** medium-large (new dataclass, new spec grammar, new preview/dashboard table column).
**Dependencies:** none.
**Confidence:** high.
**Value:** medium.

### SC-6 — Recurring-meeting end condition (occurrence count or "until" date)
**Pitch:** a meeting that stops recurring after N occurrences or a specific date, independent of the schedule's own `start_date`/`end_date` range.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** additive — optional `count: int = 0` / `until: str = ""` on `RecurringMeeting`; `0`/`""` preserves today's unbounded behaviour exactly, so none of the biweekly tests (which never set these fields) change.
**Fixtures needed:** none beyond new unit tests.
**Evidence:** axis-5a research pass.
**Size:** small-medium.
**Dependencies:** none.
**Confidence:** high.
**Value:** medium.

### SC-7 — Per-meeting blackout dates
**Pitch:** cancel one specific recurring meeting on one date without suppressing every other meeting that day — today, `exclude_dates` is config-wide and suppresses everything (`test_excluded_dates_suppress_recurring_and_oneoffs`).
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** additive — new `skip_dates: list[str] = field(default_factory=list)` on `RecurringMeeting`, defaults `[]`, existing global-exclude tests untouched (they don't exercise this new field).
**Fixtures needed:** none beyond new unit tests.
**Evidence:** axis-5a research pass.
**Size:** small.
**Dependencies:** none.
**Confidence:** high.
**Value:** medium.

### SC-8 — Holiday-calendar / exclude-dates-from-file import
**Pitch:** `--exclude-file PATH` reading a list of dates (CSV/JSON/ICS) and feeding them into `cfg.exclude_dates` via the existing `add_exclusion()`/dedupe-and-sort path.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** purely additive — reuses the existing, already-tested `exclude_dates` mechanism; unused by default, no output change for existing invocations.
**Fixtures needed:** none.
**Evidence:** axis-5a research pass.
**Size:** small.
**Dependencies:** shares parsing groundwork with SC-10 (ICS import) if `.ics` holiday calendars are the target format.
**Confidence:** high.
**Value:** medium.

### SC-9 — Export the planned schedule as CSV / iCalendar
**Pitch:** `--export PATH` on `preview` (or `submit --dry-run`), reusing the CSV-export pattern already proven in `dashboard.export()`, plus an `.ics` writer.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** purely additive — new opt-in flag, no existing output changed.
**Fixtures needed:** none.
**Evidence:** `worklog_scheduler/dashboard.py:356-381` (existing CSV/JSON precedent), axis-5a research pass.
**Size:** small (CSV/JSON) to medium (`.ics` generation).
**Dependencies:** none.
**Confidence:** high.
**Value:** medium.

### SC-10 — Import one-off meetings from an `.ics` file
**Pitch:** `--import-ics PATH` parsing VEVENT entries into `OneOffMeeting`s, appended via the existing `cfg.oneoffs.extend(...)` + `sort_oneoffs()` pattern already used for `--oneoff`.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** purely additive, symmetric with SC-9's export direction.
**Fixtures needed:** none.
**Evidence:** axis-5a research pass; `schedule_and_post_worklogs.py:179-181` (existing `--oneoff` append pattern).
**Size:** medium (ICS/timezone parsing is the real cost).
**Dependencies:** none.
**Confidence:** high on the append mechanism; medium on robust ICS/timezone edge cases.
**Value:** medium.

### SC-11 — Multiple named saved schedules/profiles
**Pitch:** `--profile NAME` resolving to `~/.jira_worklog_manager.<name>.json` instead of the single default `CONFIG_PATH`, plus a `profiles` subcommand to list/copy/delete them.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** purely additive for the default (unflagged) path — `ScheduleConfig.load`/`.save` already accept an arbitrary `path` argument (proven by the existing `config --export`/`--load` flags), so this is mostly CLI plumbing over existing capability.
**Fixtures needed:** none.
**Evidence:** `worklog_scheduler/config.py:118,188-205`; `schedule_and_post_worklogs.py:99-103,490-513` (existing `config --export`/`--load`); axis-5a research pass.
**Size:** small-medium.
**Dependencies:** none.
**Confidence:** high.
**Value:** medium.

### SC-12 — Diff-against-last-submitted-run preview
**Pitch:** on a live submit, append a small history record (entries + timestamp) to `~/.jira_worklog_manager.history.jsonl`; a new `preview --diff-last` shows added/removed/changed entries before posting.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** purely additive — new file, new opt-in flag/output block; doesn't touch `_totals_line`, the `"Planned worklogs"` table, or any pinned string since it's a new panel printed only on request.
**Fixtures needed:** none.
**Evidence:** axis-5a research pass.
**Size:** medium-large (needs a stable entry-diff key, e.g. `(issue, day, start, comment)`).
**Dependencies:** none.
**Confidence:** medium-high.
**Value:** medium.

### SC-13 — Post-submit audit log
**Pitch:** after a live submit, append one line per posted entry (issue, start, duration, comment, returned worklog id) to an append-only log file, independent of the config file.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** purely additive — new output artifact only; doesn't touch `"Posted N/M worklogs to X"` or any printed status line in `do_submit`.
**Fixtures needed:** none.
**Evidence:** axis-5a research pass.
**Size:** small.
**Dependencies:** none.
**Confidence:** high.
**Value:** low-medium (nice-to-have; overlaps somewhat with LIB-9's broader structured-logging idea).

### SC-14 — Per-meeting timezone override
**Pitch:** optional `timezone: str = ""` on `RecurringMeeting`/`OneOffMeeting`, falling back to `cfg.timezone` (today's sole source).
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** additive — default `""` preserves the single-timezone path exactly, so `test_start_times_carry_the_configured_zone` (which never sets a per-meeting override) is unaffected.
**Fixtures needed:** none.
**Evidence:** `worklog_scheduler/schedule.py:62` (single `ZoneInfo(cfg.timezone)` lookup); axis-5a research pass.
**Size:** small-medium.
**Dependencies:** none.
**Confidence:** high.
**Value:** low-medium — real for a distributed team, niche otherwise.

### SC-15 — `config diff` subcommand
**Pitch:** given two config paths, print a structured diff of recurring/oneoff/exclude entries.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** purely additive; new subcommand, reuses `ScheduleConfig.load` at arbitrary paths (already proven).
**Fixtures needed:** none.
**Evidence:** axis-5a research pass.
**Size:** medium.
**Dependencies:** pairs with SC-11 (profiles) but doesn't require it.
**Confidence:** high.
**Value:** low-medium.

### SC-16 — Speculative: bulk "shift a range of dates" helper
**Pitch:** move all one-offs/exclusions inside a date range by N days in one operation — useful when a whole week's meetings get rescheduled.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** purely additive UI/CLI convenience over existing mutation methods; doesn't touch `build_entries` or persisted schema.
**Fixtures needed:** none.
**Evidence:** axis-5a research pass.
**Size:** small.
**Dependencies:** none.
**Confidence:** high.
**Value:** low — genuinely speculative, narrow use case.

### SC-17 — Speculative, higher-risk: alternate duration syntax in specs
**Pitch:** accept `MON-FRI@10:00+1h30m=Standup` as sugar for `+90=Standup`.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** the riskiest proposal in this section — it must be layered as an *alternate* accepted duration group without changing what already matches `+30=`, or `test_parse_recurring_spec`, `test_parse_oneoff_spec`, and the negative-case tests in `test_bad_recurring_specs`/`test_bad_oneoff_specs` (which pin exact rejection of malformed specs) could regress. The intent is additive; the regex surgery to get there safely is not trivial.
**Fixtures needed:** none.
**Evidence:** axis-5a research pass.
**Size:** medium.
**Dependencies:** none.
**Confidence:** medium — doable, but regex-touching changes near a pinned-regex surface deserve extra test coverage before merging.
**Value:** low-medium — convenience only.

### SC-18/SC-19 — Speculative, opt-in-only: preview `--explain` annotation and dashboard "target vs actual" row
**Pitch:** SC-18 annotates the dry-run preview with which rule produced each entry (debugging overlapping recurring rules); SC-19 adds a `--target-hours-per-day N` summary row to the dashboard.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** both must stay strictly behind a new flag — SC-18 would otherwise alter the `"Planned worklogs"` table pinned by `test_preview_lists_entries_and_posts_nothing`; SC-19 would otherwise alter `_summary_rows()`'s exact five-row shape (used by `"Total logged"` assertions in `test_scheduler_cli.py`) if merged into the unflagged path rather than added as a separate panel.
**Fixtures needed:** none.
**Evidence:** axis-5a research pass.
**Size:** small-medium each.
**Dependencies:** none.
**Confidence:** high, contingent on staying opt-in.
**Value:** low-medium each.

### SC-20 — Auto-comment on the issue after a live submit *(depends on NT-3)*
**Pitch:** post a "Logged Xh via Trackspace" comment after a successful live submit.
**Endpoints/fields:** `POST /issue/{key}/comment` (see NT-3).
**KB status:** not gated beyond NT-3 itself.
**Frozen-behaviour impact:** additive if strictly opt-in and placed after the existing `"Posted N/M worklogs to X"` line, not replacing it.
**Fixtures needed:** shares NT-3's fixtures.
**Evidence:** axis-1a + axis-5a research passes combined (new finding at synthesis).
**Size:** small once NT-3 exists.
**Dependencies:** NT-3.
**Confidence:** high.
**Value:** medium.

---

## Features in `worklog_visualizer`

Checked against `tests/test_visualizer.py`'s pinned assertions: exact window-resolution precedence and default label (`"last 30 days"`), the no-file-written-without-`--export`invariant, the exact `COLUMNS`/DataFrame shape, and the figure's "unchanged from the original script" contract in `figure.py`'s own docstring.

### VZ-1 — Multi-user / team rollup report
**Pitch:** `--users a,b,c` (or a resolved group) fetches each user's worklogs and merges them into one combined report with a per-author breakdown.
**Endpoints/fields:** N calls to the existing `fetch_recent_worklogs`/`find_user`, or a `worklogAuthor in (...)` JQL clause — **the latter is untested ground**: `kb/jql.md`'s "Not observed" section and `kb/trackspace.json`'s two JQL templates confirm only single-value `worklogAuthor` clauses have ever been exercised against this instance; no `IN (...)` template exists.
**KB status:** not a declared unknown, but genuinely `unverified` — the safe path is N separate searches merged client-side (proven pattern), not a new untested JQL shape.
**Frozen-behaviour impact:** none — `fetch.fetch_recent_worklogs`'s existing signature stays exactly as pinned by `test_fetch_filters_by_author_and_instant` and `test_fetch_for_another_user_resolves_them_first`; this is a new orchestration layer on top.
**Fixtures needed:** a multi-user search-result fixture if the `IN (...)` path is ever attempted; otherwise none beyond existing per-user fixtures.
**Evidence:** axis-5b research pass; `kb/jql.md` (confirmed no multi-value template exists).
**Size:** medium.
**Dependencies:** none required; VZ-13 (leaderboard) is a natural pairing once this exists.
**Confidence:** high for the N-searches-merged approach; low for the untested `IN (...)` JQL shape until probed.
**Value:** high — the most-requested-shaped gap for a manager-facing use of this tool.

### VZ-2 — Raw JQL passthrough
**Pitch:** `--jql "<expr>"` lets a power user supply their own JQL fragment instead of only `--user`, combined with the existing worklog-date bounds.
**Endpoints/fields:** the existing `search` endpoint, arbitrary caller-supplied JQL.
**KB status:** explicitly new ground — `kb/jql.md`'s "Not observed" section states no clause beyond `worklogAuthor`/`worklogDate` has been tried against this instance.
**Frozen-behaviour impact:** none structurally; must still apply the existing client-side author/date filter (per `kb/quirks.md` #3/#4) since arbitrary JQL can't be trusted to scope worklogs correctly on its own.
**Fixtures needed:** none beyond a custom-JQL search-result fixture for tests.
**Evidence:** axis-5b research pass.
**Size:** medium.
**Dependencies:** none.
**Confidence:** medium — the mechanism is trivial; whether arbitrary user-supplied JQL behaves as expected against this instance is unverified.
**Value:** medium — power-user feature, real but narrower audience than VZ-1.

### VZ-3 — Compare two windows (diff view)
**Pitch:** `--vs-previous` runs two fetches (current window vs. an equal-length prior window) and renders a delta — total-hours change, new/dropped tickets, per-ticket deltas.
**Endpoints/fields:** none new — two calls to the existing fetch path.
**KB status:** not gated.
**Frozen-behaviour impact:** purely additive; the existing single-window `render_report` and all its pinned `summary_rows()` keys/values (e.g. `"Total logged": "3.0 h"`) are untouched for the single-window path. A diff figure, if exported, must be a distinct code path — `figure.py`'s own docstring says the image is "unchanged from the original script," so `build_figure` itself must not be modified.
**Fixtures needed:** none beyond a second-window fixture set.
**Evidence:** axis-5b research pass.
**Size:** medium-large (new terminal renderer; new figure layout if exported).
**Dependencies:** none.
**Confidence:** high.
**Value:** medium-high.

### VZ-4 — Drill into one ticket's full worklog history
**Pitch:** `--ticket CLOPSSEC-41501` shows every entry on one ticket, every author, chronological, instead of the aggregate report.
**Endpoints/fields:** `client.issue_worklogs(key)` directly — already exists; skips the search phase and doesn't apply the `target_ids` author filter.
**KB status:** not gated.
**Frozen-behaviour impact:** none — no pinned assertion concerns a single-ticket view.
**Fixtures needed:** none beyond existing per-issue worklog fixtures.
**Evidence:** axis-5b research pass; note it must not repeat quirk #8's single-page bound (see SC-1) on a heavily-logged ticket — use the paginating call, which `fetch.py` already does by default.
**Size:** small-medium.
**Dependencies:** none.
**Confidence:** high.
**Value:** medium.

### VZ-5 — Calendar-heatmap terminal rendering
**Pitch:** a GitHub-contributions-style grid (weeks as columns, days as rows, colour intensity = hours) as an alternate or additional view alongside the existing stacked-bar timeline.
**Endpoints/fields:** none new — pure rendering over data the tool already fetches.
**KB status:** not gated.
**Frozen-behaviour impact:** none if added as a new renderer function; `test_timeline_collapses_tickets_beyond_the_eighth` and `test_long_windows_bucket_by_week_then_month` pin the *existing* `_render_timeline`/`_bucket` behaviour, which stays default.
**Fixtures needed:** none.
**Evidence:** axis-5b research pass.
**Size:** medium (new `rich`-based renderer using `trackspace.ui.charts`).
**Dependencies:** none.
**Confidence:** high.
**Value:** medium.

### VZ-6 — Replay a previously-exported file as report input *(depends on LIB-10)*
**Pitch:** `--from-file rows.json` skips Trackspace entirely and re-renders the terminal report (or re-exports an image) from previously exported rows — useful for demos or offline re-rendering.
**Endpoints/fields:** none — no network call at all on this path.
**KB status:** not gated.
**Frozen-behaviour impact:** none on the live path; the loader must reproduce the exact DataFrame shape pinned by `test_export_can_write_rows` (`{"date","ticket_id","summary","hours","author"}`) and `test_fetch_filters_by_author_and_instant`'s column-order assertion.
**Fixtures needed:** none beyond existing export-format fixtures.
**Evidence:** axis-5b research pass; axis-6 (composition) confirms today's export is write-only in both tools — "replay" doesn't exist as a capability anywhere yet, so this is new import-side code, not a small fix.
**Size:** small-medium.
**Dependencies:** benefits from LIB-10 (schema reconciliation) if this is meant to also accept the *scheduler's* export format, not just the visualiser's own.
**Confidence:** high for accepting the visualiser's own export shape; see LIB-10 for the cross-tool case.
**Value:** medium.

### VZ-7 — Persist last-used window/user as a profile
**Pitch:** analogous to the scheduler's `~/.jira_worklog_manager.json`, remember the last `--ago`/`--date`/`--user` choice — confirmed today the visualiser persists **nothing** between runs (see axis-6, composition).
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** **the one visualiser proposal needing real care.** `test_resolve_window_prefers_datetime_then_date_then_ago` pins that with no flags, the resolved window is `label == "last 30 days"` — a saved profile silently overriding the default would break this test outright unless the feature is opt-in (e.g. `--use-profile`). Separately, `test_no_file_is_written_without_an_export_flag` asserts `list(tmp_path.iterdir()) == []` after a plain run — a profile write on every run would violate this unless the profile lives outside the CWD (e.g. `~/`, matching the scheduler's own pattern) and/or is opt-in.
**Fixtures needed:** none.
**Evidence:** axis-5b research pass (confirmed via grep: no config/save/load logic exists anywhere in `worklog_visualizer` today); axis-6 (composition) independently confirmed the same asymmetry.
**Size:** medium (new module mirroring `worklog_scheduler/config.py`'s pattern).
**Dependencies:** none, but design must resolve the two test-collision risks above before implementation, not after.
**Confidence:** high on the mechanism; the risk is entirely in getting the default-vs-opt-in decision right.
**Value:** medium.

### VZ-8 — Additional export format: Markdown table / plain-text summary
**Pitch:** a third export family (`.md`/`.txt`) — a Markdown table built from the same rows `_render_tickets` already computes, for pasting into a Jira comment or Slack.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** none on `IMAGE_SUFFIXES`/`DATA_SUFFIXES` handling — new suffix branch. Must keep `test_unknown_export_format_is_a_config_error` passing (it tests `.txt` specifically, which would need to move from "unsupported" to "supported" deliberately, not accidentally).
**Fixtures needed:** none.
**Evidence:** axis-5b research pass.
**Size:** small.
**Dependencies:** pairs naturally with NT-3/NT-4 (something worth pasting into a comment or attaching).
**Confidence:** high.
**Value:** medium.

### VZ-9 — Non-interactive "watch" / auto-refresh mode
**Pitch:** `--watch 60s` re-runs fetch+render on an interval — a dashboard-style always-on terminal view.
**Endpoints/fields:** none new — wraps the existing `run_report` in a loop.
**KB status:** not gated.
**Frozen-behaviour impact:** none — single-run behaviour (and its exit-code/message assertions) is unchanged for one iteration.
**Fixtures needed:** none.
**Evidence:** axis-5b research pass.
**Size:** small-medium.
**Dependencies:** none. Interacts with the unwitnessed rate-limit policy (KBW-6) — repeated polling is exactly the pattern that would first expose real throttling, so this is worth building only after (or alongside) a rate-limit probe.
**Confidence:** high.
**Value:** low-medium.

### VZ-10 — Idle-gap / streak detection panel
**Pitch:** "longest gap with no logging" and "current logging streak," computed from the existing `daily_totals()` helper.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** additive only if placed in a **new, separate panel** — `test_summary_rows_match_the_figure_stats` pins the exact five-row shape and exact string formats of `summary_rows()`; a sixth row would technically still pass that test (it doesn't assert row count) but would drift from `figure.py`'s explicitly "unchanged" five-line summary panel, so keep this as its own panel rather than appending to the shared one.
**Fixtures needed:** none.
**Evidence:** axis-5b research pass.
**Size:** small.
**Dependencies:** none.
**Confidence:** high.
**Value:** low-medium.

### VZ-11 — Configurable timezone override (`--tz`)
**Pitch:** let `--date`/`--datetime` be interpreted in an explicit zone rather than always `LOCAL_TZ` (OS-local), useful for a distributed team filing against a shared reference zone.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** additive as an optional flag; default (no `--tz`) must keep using `LOCAL_TZ` exactly as today — multiple tests already exercise specific-zone behaviour (`BERLIN = ZoneInfo("Europe/Berlin")` fixtures), so care is needed not to change the *default* resolution.
**Fixtures needed:** none.
**Evidence:** axis-5b research pass; `worklog_visualizer/window.py` (`LOCAL_TZ` module-level).
**Size:** small.
**Dependencies:** none.
**Confidence:** high.
**Value:** low-medium.

### VZ-12 — Project/ticket include-exclude filters
**Pitch:** `--project CLOPSSEC` / `--exclude-ticket KEY-123`, a client-side filter applied after fetch, narrowing the DataFrame before rendering.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** none — a post-fetch DataFrame filter; `render_report`/`export()` don't care how the DataFrame was produced, only its column shape.
**Fixtures needed:** none.
**Evidence:** axis-5b research pass.
**Size:** small.
**Dependencies:** none.
**Confidence:** high.
**Value:** medium.

### VZ-13 — Author-ranked leaderboard panel *(pairs with VZ-1)*
**Pitch:** within a team rollup, rank users by total hours in the window — the natural complement to the existing per-ticket ranking in `_render_tickets`.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** none — purely new, only meaningful once VZ-1 exists.
**Fixtures needed:** shares VZ-1's fixtures.
**Evidence:** axis-5b research pass.
**Size:** small once VZ-1 lands.
**Dependencies:** VZ-1.
**Confidence:** high.
**Value:** medium.

### VZ-14/VZ-15/VZ-16 — Speculative: anomaly threshold flag, `.ics` export of entries, shareable "report card" summary export
**Pitch:** VZ-14 highlights days crossing a user-supplied hours threshold; VZ-15 exports each worklog entry as a calendar-event block for cross-checking against a real calendar; VZ-16 exports the *computed* summary (not raw rows) as structured JSON for downstream tooling, via a distinct flag so it doesn't collide with the existing raw-row `.json` export.
**Endpoints/fields:** none new for any of the three.
**KB status:** not gated.
**Frozen-behaviour impact:** VZ-14/VZ-15 additive if new overlays/export suffixes. VZ-16 **must not reuse the existing `.json` export suffix** — `test_export_can_write_rows`'s exact row-count and column-set assertions are about raw rows; a computed-summary export needs its own flag (e.g. `--export-summary`).
**Fixtures needed:** none for VZ-14/15; a new summary-shape fixture for VZ-16.
**Evidence:** axis-5b research pass, explicitly labelled speculative there.
**Size:** small (VZ-14), small-medium (VZ-15), medium (VZ-16).
**Dependencies:** none.
**Confidence:** high on mechanism, low on demand — flagged speculative by the research pass itself, kept here rather than dropped per the coverage bar.
**Value:** low for all three; included for completeness.

### VZ-17 — Sprint-scoped window *(gated on Jira Software licensing, unverified)*
**Pitch:** `--sprint current` (or a named sprint) as an alternative to `--ago`/`--date`/`--datetime`, using a sprint's `startDate`/`endDate` as the window bounds.
**Endpoints/fields:** `GET /rest/agile/1.0/board`, `.../sprint`, `.../sprint/{id}` (all Agile-1.0, confirmed to exist as an API surface generally).
**KB status:** **hard-gated on an unrecorded fact**: whether Trackspace's Data Center install has Jira Software licensed/enabled at all. Nothing in `kb/instance.md` or `kb/trackspace.json` confirms this either way. A single defensive `GET /rest/agile/1.0/board` call (expect either a normal list or a clean 404/"not licensed" response) must run and be recorded before this is scoped further.
**Frozen-behaviour impact:** none — new window option, additive to `resolve_window`.
**Fixtures needed:** `agile_board_list.json` (and a "not licensed" 404 variant, since the outcome is genuinely unknown).
**Evidence:** axis-1c research pass, explicit about the Jira Software licensing caveat.
**Size:** medium, contingent on the licensing question.
**Dependencies:** a probe check (could ride along with NT-1) before any real design work.
**Confidence:** low until the licensing question is answered — this is the single most availability-uncertain item in the whole catalogue.
**Value:** unknown until gated fact is resolved; potentially high for a sprint-based team, zero if Jira Software isn't licensed here.

### VZ-18 — Saved-filter picker *(pairs with VZ-2)*
**Pitch:** let a user pick from their saved/favourite Jira filters (`GET /filter/favourite` or `/filter/my`) instead of retyping a JQL string every run.
**Endpoints/fields:** `GET /rest/api/2/filter/favourite`, `GET /rest/api/2/filter/my` — confirmed DC v2, core Jira, no Jira Software needed (unlike VZ-17).
**KB status:** not gated — new but unambiguously available surface.
**Frozen-behaviour impact:** none — new interactive picker step, additive.
**Fixtures needed:** `filter_favourite_list.json`.
**Evidence:** axis-1c research pass; `developer.atlassian.com/server/jira/platform/rest/v10007/api-group-filter/`.
**Size:** small-medium.
**Dependencies:** most useful alongside VZ-2 (raw JQL passthrough), since a saved filter *is* a piece of JQL.
**Confidence:** high.
**Value:** medium.

---

## Shared library capability

Grounded in the shared-library exploration's inventory of what's built but idle: `chrome.grouped`, `chrome.console_pair` (unused by either tool), `charts.sparkline` (visualiser-only), `prompts.checkbox`/`allow_back` (scheduler-only), `client.ProgressCallback`/`on_progress` (defined, never passed by either tool), and `KnowledgeBase.default()`/`.raw`/`.fixture()`/`.endpoint_names()` (test-only, no tool call site). Confirmed: no caching beyond the KB-document `lru_cache`, no structured logging, no `--json` mode, no run manifests anywhere.

### LIB-1 — Generalize the KB beyond one hardcoded project
**Pitch:** `kb/trackspace.json`'s `projects` section has exactly one entry, `CLOPSSEC`, with its name marked unknown. Once NT-1/KBW-4 records real project metadata, generalize `projects` into a reusable "resolve any project key → display name/issue types" capability rather than a single hardcoded row.
**Endpoints/fields:** `GET /project/{key}` (see NT-1).
**KB status:** directly extends a declared unknown once resolved.
**Frozen-behaviour impact:** none — additive KB schema extension; existing `projects.CLOPSSEC` entry stays valid as one row among many.
**Fixtures needed:** shares NT-1's `project_get` fixtures, generalized to more than one project key if a second project is ever exercised.
**Evidence:** `kb/fields.md` ("Projects" table, single-row); axis-2 (probe design) research pass.
**Size:** small once NT-1 exists.
**Dependencies:** NT-1/KBW-4.
**Confidence:** high.
**Value:** medium — mostly matters if a future tool needs to work across more than one project.

### LIB-2 — Wire `ProgressCallback` through both tools
**Pitch:** `client.py`'s `iter_search_issues`/`search_issues` already accept an `on_progress` callback; neither tool passes one — both call the eager `search_issues(...)` wrapper with no progress hook, even though `LiveStatus` already exists to display exactly this kind of counter.
**Endpoints/fields:** none new — pure wiring.
**KB status:** not gated.
**Frozen-behaviour impact:** none — additive UX improvement (a live "N issues found" counter during a search that currently just shows a spinner with no count until the whole search completes).
**Fixtures needed:** none.
**Evidence:** shared-library exploration (confirmed zero `on_progress=` call sites outside the client's own tests); `trackspace/client.py:32,211-247`.
**Size:** small.
**Dependencies:** none.
**Confidence:** high.
**Value:** low-medium — small polish, cheap.

### LIB-3 — Retire or adopt `chrome.grouped`/`chrome.console_pair`
**Pitch:** two exported functions with zero call sites anywhere, including tests. Either find them a real use (e.g. `console_pair()`'s stdout/stderr split could back a future `--json`-to-stdout-plus-status-to-stderr mode, see LIB-6) or remove them as dead code — a hygiene call as much as a capability one.
**Endpoints/fields:** none.
**KB status:** not gated.
**Frozen-behaviour impact:** none either way — nothing currently depends on these.
**Fixtures needed:** none.
**Evidence:** shared-library exploration, explicit "unused by either tool" finding for both.
**Size:** trivial (removal) or small (adoption, contingent on LIB-6).
**Dependencies:** LIB-6 if adopting rather than removing.
**Confidence:** high.
**Value:** low — cleanliness only, but essentially free.

### LIB-4/LIB-5 — Bring the other tool's UI-kit usage to parity
**Pitch:** `prompts.checkbox(..., allow_back=True)` is scheduler-only (no analogous multi-select exists yet in the visualiser — VZ-1's team picker would be the natural first consumer); `charts.sparkline` is visualiser-only (the scheduler's dashboard has no equivalent shape indicator).
**Endpoints/fields:** none.
**KB status:** not gated.
**Frozen-behaviour impact:** none — both are additive uses of already-built, already-tested shared-library code.
**Fixtures needed:** none.
**Evidence:** shared-library exploration.
**Size:** small each, and only meaningful alongside a consuming feature (VZ-1 for LIB-4, any scheduler timeline addition for LIB-5).
**Dependencies:** LIB-4 depends on VZ-1 existing to have something to check-box-select; LIB-5 has no hard dependency.
**Confidence:** high.
**Value:** low — these aren't gaps so much as "the library already supports this, a consumer just hasn't needed it yet."

### LIB-6 — `--json` output mode
**Pitch:** a machine-readable output mode (piping either tool's results into another tool or a script) — confirmed to not exist anywhere today; all output is either rich-console rendering or the existing `.json`/`.csv` *export* files (which require `--export` and write to disk, not stdout).
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** additive if implemented as a wholly new flag (`--json` printing to stdout instead of the rich console); must not change default terminal output at all.
**Fixtures needed:** none.
**Evidence:** shared-library exploration (explicit confirmation: "no `--json` flag or machine-readable output path found... aside from the CSV-export calls").
**Size:** medium (needs a stable schema decision, ideally shared with LIB-10 below).
**Dependencies:** benefits from LIB-10 (shared row schema) to avoid inventing yet a third incompatible shape.
**Confidence:** high.
**Value:** medium — the platform's own README already claims "the same tool runs non-interactively in CI," which a piping-friendly output mode would make more literally true.

### LIB-7 — Shared session/identity concept
**Pitch:** both tools independently call `read_pat()`/`auth_status()` and build their own client every run; the visualiser additionally honors `JIRA_BASE_URL`/`JIRA_API_VERSION`/`JIRA_AUTH_TYPE`/`JIRA_EMAIL` env overrides that the scheduler's `make_client()` does not — a second, smaller asymmetry alongside the config-persistence one. A shared `trackspace/session.py` could unify base-URL/auth-presence/last-picked-issue-or-user across both tools.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** additive if both tools keep reading raw env vars as a fallback; the auth-override asymmetry (visualiser honors more env vars than scheduler) needs a deliberate decision, not a silent merge, since reconciling it changes what environment variables the scheduler responds to.
**Fixtures needed:** none.
**Evidence:** axis-6 (composition) research pass — confirmed via direct comparison of the two `make_client()` implementations.
**Size:** medium.
**Dependencies:** none, but interacts with VZ-7 (visualiser profile) and SC-11 (scheduler profiles) — worth sequencing after those settle, not before, to avoid building a session concept around two still-diverging config shapes.
**Confidence:** high the asymmetry exists; medium on the right unification design.
**Value:** medium.

### LIB-8 — Response-recording mode that generates fixtures from a real run
**Pitch:** an opt-in mode where the client records real responses to disk in the exact shape `kb/fixtures/` already expects — would sharply cut the cost of every new-endpoint recommendation in this report (NT-1 through NT-6, VZ-17/18), each of which currently needs hand-authored fixtures.
**Endpoints/fields:** none new — a client-level capability, not tied to any specific endpoint.
**KB status:** not gated; this is exactly the tool that would make the KB unknowns cheap to resolve and record going forward.
**Frozen-behaviour impact:** none — strictly opt-in, off by default; must never fire during normal test runs (which use `FakeSession`, not a real network call, so this mode is inherently inert under `pytest`).
**Fixtures needed:** ironically, none — this *produces* fixtures rather than needing them. What only production can confirm: real response shapes for every endpoint this mode is pointed at.
**Evidence:** shared-library exploration (confirmed no such mechanism exists — `KnowledgeBase.fixture()` only *reads* hand-authored files, never writes them); speculative synthesis connecting that gap to the audit's own recurring "new fixtures needed" cost.
**Size:** medium.
**Dependencies:** none, but highest leverage if built before NT-1 rather than after (turns NT-1's probe run directly into ready fixtures instead of a findings report someone then hand-encodes).
**Confidence:** high the gap exists; medium on the right recording format (need to redact the PAT and any real personal data before anything gets committed as a fixture — this is the one place accidental exposure risk from HYG-5 could reappear if built carelessly).
**Value:** high leverage, given how many other entries in this report cite "new fixture needed" as their main cost.

### LIB-9 — Structured/JSONL run logging
**Pitch:** nothing in the platform writes to a log file today — every diagnostic is a `rich.Console` print, gone once the terminal scrolls. A `--log-file PATH` writing one JSON line per operation (search issued, worklog posted, warning raised) would give both tools an audit trail independent of the terminal.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** additive, strictly opt-in.
**Fixtures needed:** none.
**Evidence:** shared-library exploration (explicit confirmation: no `import logging` anywhere in the repo outside third-party packages).
**Size:** small-medium.
**Dependencies:** overlaps with SC-13 (scheduler-specific audit log) — if built at the library level, SC-13 becomes "turn this flag on" rather than a bespoke scheduler feature.
**Confidence:** high.
**Value:** medium.

### LIB-10 — Reconcile the two tools' export schemas
**Pitch:** `worklog_scheduler/dashboard.py`'s export (`key`, `summary`, `date`, `hours`, `comment`) and `worklog_visualizer`'s export (`date`, `ticket_id`, `summary`, `hours`, `author`) are **not compatible** — different ticket-id field name (`key` vs `ticket_id`), mutually exclusive fields (`comment` present only in one, `author` only in the other), different CSV header order. Feeding one tool's export into the other today is not possible without a mapping step.
**Endpoints/fields:** none new — a schema-design question, not an API question.
**KB status:** not gated.
**Frozen-behaviour impact:** **this is the one place in the whole audit where "purely additive" does not fully hold.** Unifying field names by renaming existing ones would be a breaking change to both tools' current export format — any downstream consumer of today's `key`/`comment` CSV or `ticket_id`/`author` CSV would need to adapt. The additive path is to add a *new*, third canonical shape that both tools can optionally emit alongside their existing exports, not to rename the existing fields out from under `test_export_can_write_rows` and the scheduler's equivalent.
**Fixtures needed:** a new shared fixture for the reconciled shape.
**Evidence:** axis-6 (composition) research pass — direct side-by-side field comparison of both `export()` functions.
**Size:** medium.
**Dependencies:** blocks VZ-6's cross-tool ambitions and LIB-6's stdout-JSON design decision; best resolved before either is built, not after.
**Confidence:** high that the incompatibility exists exactly as described; medium on which unification design is right.
**Value:** medium-high as a prerequisite, low in isolation — this is infrastructure for other entries, not a user-facing feature on its own.

### LIB-11 — HTTP response caching
**Pitch:** confirmed no response caching exists anywhere — `kb.py`'s `@lru_cache` memoizes the *parsed KB document*, not any Jira API response; every `request_json` call hits the network fresh. A short-lived, opt-in cache (e.g. for `/myself`, which never changes within a run) could cut redundant calls in flows that call it more than once.
**Endpoints/fields:** none new.
**KB status:** not gated.
**Frozen-behaviour impact:** none if scoped to idempotent GETs with an explicit opt-in and short TTL; must never cache `add_worklog` (already non-cacheable by nature — it's a POST).
**Fixtures needed:** none.
**Evidence:** shared-library exploration (explicit confirmation, `client.py` grep for caching returned nothing beyond the KB-document `lru_cache`).
**Size:** small.
**Dependencies:** none.
**Confidence:** high.
**Value:** low — real but marginal; the platform's calls are already parsimonious relative to what caching would save.

---

## KB work

### KBW-1 through KBW-4 — Resolve the four "unrecorded" facts via NT-1
**Pitch:** custom field ids (`GET /field`), issue types (`GET /project/{key}` + `GET /issue/createmeta`), workflow states (`GET /status` + `GET /issue/{key}/transitions`), and the CLOPSSEC project's real name (`GET /project/CLOPSSEC`) — all four resolved by the same probe tool, NT-1.
**KB status:** all four are the exact declared unknowns quoted verbatim at the top of this document (confirmed live against `kb/trackspace.json`).
**Frozen-behaviour impact:** none — pure KB enrichment, adds `source` citations to previously-unknown fields.
**Fixtures needed:** see NT-1.
**Evidence:** NT-1; `kb/trackspace.json`.
**Size:** small each, once NT-1 exists (this is NT-1's actual output, not separate work).
**Dependencies:** NT-1.
**Confidence:** high the mechanism resolves them; unknown what the values will actually be.
**Value:** high — see the shortlist.

### KBW-5 — Record the real error body shape
**Pitch:** currently `errors.body_shape` is `source: inferred` — the assumed `{"errorMessages":[...],"errors":{...}}` shape has never been observed; only that the raw body gets sliced to 200 chars is confirmed. One deliberate GET against a bogus issue key (`GET /issue/BOGUS-1`, or `.../worklog` on a bogus key) captures it.
**KB status:** declared `inferred`, confirmed live.
**Frozen-behaviour impact:** none — the existing 200-char-slice behaviour (`kb/quirks.md`, pinned by `"HTTP 400"` and `test_error_message_matches_the_original_format`) is unaffected; this only adds evidence for the *shape* underneath the slice.
**Fixtures needed:** `issue_get_not_found.json` (see NT-1), plus optionally a malformed-key (400) variant to distinguish it from the well-formed-but-missing (404) case.
**Evidence:** `kb/pagination-and-errors.md`; axis-2 (probe design) research pass.
**Size:** small.
**Dependencies:** rides along with NT-1.
**Confidence:** high.
**Value:** medium — mostly unlocks better structured-error UX later (e.g. surfacing `errors: {field: message}` on a rejected worklog POST instead of the opaque 200-char slice).

### KBW-6 — Probe and (honestly) bound rate-limit behaviour
**Pitch:** `errors.rate_limiting.source` is `inferred` — the client's entire retry/backoff policy (`DEFAULT_MAX_RETRIES=3`, `DEFAULT_BACKOFF_BASE_S=0.5`, `MAX_BACKOFF_S=30.0`, `Retry-After` parsing) has never been validated against a real 429. A small, hard-capped burst against `/myself` observes actual headers/status without meaningfully load-testing the instance.
**KB status:** declared `inferred`, confirmed live.
**Frozen-behaviour impact:** none directly from the probe itself. If the probe's findings later justify *tuning* the retry constants, that would touch `client.py`'s retry tests (`test_rate_limit_is_retried_and_honours_retry_after`, `test_server_error_gives_up_eventually`) — but tuning is a separate, evidence-driven follow-up, not part of the probe.
**Fixtures needed:** none for the probe itself; a new 429 fixture only if a real one is ever captured (replacing the currently-synthetic one in `kb/fixtures/errors.json`).
**Evidence:** `trackspace/client.py:1-9,34-37`; `kb/quirks.md` #15; axis-3 (quirks) and axis-2 (probe design) research passes, independently converging on the same recommendation.
**Size:** small (the probe itself); the honest-framing discipline (axis-2's phrase: "0/N 429s at burst=N on `<date>`", not "confirmed") matters more than the mechanism.
**Dependencies:** rides along with NT-1.
**Confidence:** high the probe works; the finding itself is unknown until run — that's the point.
**Value:** medium — mostly risk-reduction (informs whether retry constants are too loose or too tight) rather than a new capability.

### KBW-7 — Record `/mypermissions` to explain 403s
**Pitch:** turn today's blunt `"Forbidden (403). Token lacks permission for this resource."` into a message naming the actual missing permission, and enable a pre-flight "can I do this" check before a mutation.
**KB status:** not a declared unknown per se, but a new capability that directly improves an existing, currently-opaque error path.
**Frozen-behaviour impact:** if wired into the *existing* 403 message, this would touch `ForbiddenError`'s current wording — pinned nowhere as an exact string in the tests reviewed (only 401/403's general handling is tested, not the literal message text for 403 specifically, per the drift-verification pass), so this is likely additive but worth confirming against the exact test before implementing.
**Fixtures needed:** `mypermissions_CLOPSSEC.json` (see NT-1).
**Evidence:** axis-2 (probe design) and axis-1b (relationships/metadata) research passes, independently converging.
**Size:** small.
**Dependencies:** rides along with NT-1.
**Confidence:** high.
**Value:** medium.

### KBW-8 — Add `ORDER BY` to the existing JQL templates
**Pitch:** not a new capability — a correctness fix to existing behaviour. Neither of the two JQL templates in `kb/trackspace.json` (`worklogs_by_current_user_in_range`, `worklogs_by_named_user_in_range`) carries an explicit sort, and both tools page by offset. Without a stable order, a search racing concurrent issue edits could skip or duplicate rows across pages — a real, if rare, correctness gap.
**Endpoints/fields:** the existing `search` JQL templates, plus `ORDER BY key ASC` (or `id ASC`) — core JQL, no new endpoint.
**KB status:** the templates already exist and are known (not an unknown) — this is a one-line addition to a known fact, not resolving an unknown.
**Frozen-behaviour impact:** **touches the exact JQL string produced by `KnowledgeBase.jql(...)`**, which several tests assert on directly — e.g. `test_search_call.params["jql"]` substring checks in `tests/test_visualizer.py` and `tests/test_client.py` would need their expected strings updated to include the new `ORDER BY` clause. This is the one KB-work item that is *not* purely additive by the letter of constraint 2, even though the underlying search *results* for existing inputs shouldn't change (a stable sort of the same result set is still the same result set) — flagging honestly rather than claiming zero impact.
**Fixtures needed:** none new; existing search fixtures are unaffected in content, only the request's `jql` param changes.
**Evidence:** axis-1b (relationships/metadata) research pass — the one explicitly bug-adjacent finding in the whole audit, not framed as a proposal by that pass but as a "worth calling out" correctness note, promoted to its own entry here.
**Size:** small (one string template change) plus updating the JQL-substring assertions it touches.
**Dependencies:** none.
**Confidence:** high that `ORDER BY` is valid core JQL; high that the current gap is real.
**Value:** medium — low-visibility but genuine correctness improvement, cheap to make.

---

## Hygiene

### HYG-1/HYG-2 — CI and pre-commit enforcement
**Pitch:** confirmed absent — no `.github/workflows`, no `.gitlab-ci.yml`, no `.pre-commit-config.yaml`, no secret-scanning config of any kind anywhere in the repo. The documented quality gate (`ruff`, `mypy --strict`, `bandit`, `pip-audit`, `pytest`) is entirely manual — nothing blocks a commit or push if a contributor skips it.
**Endpoints/fields:** n/a.
**KB status:** n/a.
**Frozen-behaviour impact:** none — pure tooling addition; the checks it would run are the exact ones already documented and already passing.
**Fixtures needed:** none.
**Evidence:** axis-8 (hygiene) research pass, exhaustive file-presence check; `CLAUDE.md`/`README.md`'s own "Quality gate" sections, which state the manual nature explicitly.
**Size:** small (CI: one GitHub Actions workflow running the five already-documented commands) to small-medium (pre-commit: a config file plus the one-time friction of everyone installing hooks).
**Dependencies:** none.
**Confidence:** high.
**Value:** high, low cost — the gap between "documented gate" and "enforced gate" is the single cheapest fix in this report.

### HYG-3/HYG-4 — Secret-scanning config and `.gitignore` credential patterns
**Pitch:** no secret-scanning tool config exists, and `.gitignore` has no rule excluding `.env`/credential-shaped files. **Neither reflects an actual leak** — confirmed no real secret exists anywhere in the tree or history (every token/PAT/password-adjacent match is either documentation or an explicitly fake test value like `"test-token"`/`"s3cr3t"`), and no credential file was ever committed. Both are forward-looking defense-in-depth, not remediation of a current problem.
**Endpoints/fields:** n/a.
**KB status:** n/a.
**Frozen-behaviour impact:** none.
**Fixtures needed:** none.
**Evidence:** axis-8 (hygiene) research pass — exhaustive grep for secret-shaped strings across the working tree, explicit confirmation of the fake-vs-real distinction, and a `.gitignore` content check.
**Size:** trivial (a `.gitignore` line) to small (a gitleaks config + optional CI wiring, pairing naturally with HYG-1).
**Dependencies:** none.
**Confidence:** high.
**Value:** medium — cheap insurance, not urgent.

### HYG-5 — Instance-identifying exposure: report only, decision deferred
**Pitch:** `trackspace.lhsystems.com`, `CLOPSSEC`, `adam.papp`, and `lhsystems.int` all appear across dozens of files (docs, KB, fixtures, tests) and were **all introduced at the repository's 2nd/3rd commit** — the foundational "Build the Trackspace tooling monorepo" commit that essentially everything since is built on top of. `adam-gabor.papp` never appears anywhere. No real secret was found (see HYG-3).
**Endpoints/fields:** n/a.
**KB status:** n/a.
**Frozen-behaviour impact:** none — this is a reporting item, not a proposed change. **Explicitly not recommending any action here** — whether `trackspace.lhsystems.com`/`CLOPSSEC`/`adam.papp` are acceptable to keep (they're arguably not secrets, just identifying detail for what is presented as an internal example) is a decision for whoever owns this repo, not something to pre-empt.
**Fixtures needed:** none.
**Evidence:** axis-8 (hygiene) research pass — full working-tree grep with file:line citations, plus `git log --all -S"<string>"` pickaxe search confirming exactly which commits introduced each string.
**Size:** if ever pursued: **would require a git history rewrite** (e.g. `git filter-repo`), not a simple find-and-replace on the tip commit — the strings are load-bearing in nearly every subsequent commit, not confined to an isolatable later one. Genuinely destructive and disruptive to any existing clones/forks; not something to do casually.
**Dependencies:** none.
**Confidence:** high on the facts; this entry deliberately makes no value/cost judgment on whether to act.
**Value:** n/a by design — informational.

### HYG-6 — Close documentation-completeness gaps
**Pitch:** the drift-verification pass found several flags implemented in code but not individually named in `README.md` (`--config`, `--no-save`, `--issue`, `--base-url`, `--timezone`, the `--range` choice set, `dashboard --export`, `config --load`/`--save`) — covered only by README's general "every interactive editor has a flag equivalent" statement, not a defect but a completeness gap — plus one real coverage gap: `CLAUDE.md` documents the scheduler's exit codes but never restates the visualiser's (which README does cover). No contradictions were found, only omissions and paraphrase differences.
**Endpoints/fields:** n/a.
**KB status:** n/a.
**Frozen-behaviour impact:** none — pure documentation editing.
**Fixtures needed:** none.
**Evidence:** axis-7 (drift verification) research pass — direct README/CLAUDE.md-vs-argparse comparison with line citations on both sides.
**Size:** small.
**Dependencies:** none.
**Confidence:** high.
**Value:** low-medium — cheap and safe, zero behaviour risk, purely a readability improvement.

---

## What was checked and found clean

Recorded so these don't get re-investigated later: every fixture named in `kb/trackspace.json`'s `endpoints.*.fixture` field exists in `kb/fixtures/`; the "extra" fixtures beyond those five (per-issue worklog variants, empty/malformed/error cases) are documented in `kb/fixtures/README.md` and routed by `tests/conftest.py`, with `test_kb.py` special-casing only the second search page — a deliberate, working pattern, not a gap. `pyproject.toml`'s two console-script entry points both resolve to real `main()` functions; all six spot-checked dependencies (`requests`, `rich`, `questionary`, `pandas`, `matplotlib`, `python-dateutil`) are genuinely imported where declared. Every documented exit code matches its `EXIT_*` constant and actual usage in both tools. The scheduler's post-submit auto-dashboard (already existing, internal composition — confirmed *not* a new idea) is unaffected by anything in this report.

---

## Ranked shortlist: what to build first, and why

1. **NT-1 (instance probe) first, unconditionally.** It's the single highest-leverage item in this report — cheap (GET-only, ~7 endpoints, no frozen-behaviour risk), and it's the prerequisite for KBW-1 through KBW-7, LIB-1, NT-2, NT-3's field-aware extensions, and VZ-17's licensing question. Everything downstream of "what custom fields/issue types/workflow states does this instance actually have" is currently guesswork-blocked without it.
2. **SC-1 (paginate the scheduler's own dashboard) second.** The mechanism is already built and already proven by the visualiser — this is the cheapest real fix in the report (drop one keyword argument, add a test fixture) and it fixes a genuine, silent undercounting bug on any heavily-logged issue.
3. **HYG-1 (CI) third, in parallel with the above.** Independent of everything else, costs almost nothing (the five commands are already documented and already passing), and closes the gap between "there is a quality gate" and "the quality gate is enforced" — the kind of fix that gets more valuable the longer it's deferred, since every commit made without it is a commit that could have silently broken the gate.
4. **SC-2 (read-only preflight warning before posting)** next — directly addresses the risk the client's own docstring names explicitly ("a retry... double-books the time"), stays strictly additive (a GET before the existing POST, no change to the POST itself), and is cheap once NT-1's fixture-generation patterns (or LIB-8, if built) make the extra test fixture easy to produce.
5. **VZ-1 (multi-user rollup)** and **SC-4 (multi-issue schedules)** as the next tier — both are the most-clearly-requested-shaped gaps (one tool assumes one user, the other assumes one issue) and both are purely additive with medium, well-understood cost. Sequence VZ-1 before VZ-13 (leaderboard) and SC-4 before nothing in particular — it stands alone.
6. **LIB-10 (reconcile export schemas)** before VZ-6 or LIB-6 are attempted — it's the one entry in this report that isn't purely additive if done by renaming existing fields, so worth deciding deliberately and early rather than working around it twice in two different features later.

Everything else in the catalogue is real, cited, and worth having — but lower leverage, more speculative, or blocked on one of the above landing first.
