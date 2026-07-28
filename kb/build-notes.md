# Build notes — capability-audit subset build

Running notes for the scoped build of: NT-1 (+KBW-1..6), NT-2..NT-6 (merged into
one package), SC-1, SC-7, SC-8, SC-9, SC-10, SC-17, SC-18, SC-19, VZ-2, LIB-2,
LIB-10, HYG-1..5. One lesson per item as it lands. Specs live in
`kb/proposals/capability-audit.md` under each ID.

## Orchestration state (updated as phases complete)

- [x] **Phase 0 — foundation (single-writer choke points)**: new KB endpoint
  entries in `kb/trackspace.json` (probe + companion endpoints, paths/methods
  only — response shapes stay unverified until probed), all new fixtures in
  `kb/fixtures/`, routing in `tests/conftest.py`, rows in
  `kb/fixtures/README.md`. `tests/test_kb.py`'s method whitelist extended to
  GET/POST/PUT/DELETE (meta-test, not pinned behaviour). Commit.
- [x] **Phase 1 — parallel packages** (agents; none may touch
  kb/trackspace.json, tests/conftest.py, kb/fixtures/*, README.md, CLAUDE.md):
  - [x] NT-1: `instance_probe/` package + `tests/test_probe.py`
  - [x] NT-2..6: `issue_companion/` package + `tests/test_issue_companion.py`
    (+ multipart/headers support in `trackspace/client.py` — that agent owns
    client.py). Transition *execute* path built later, once the 2026-07-28
    probe run satisfied its gate.
  - [x] SCHED: SC-1, SC-7, SC-8, SC-9, SC-10, SC-17, SC-18, SC-19 in
    `worklog_scheduler/` + appends to tests/test_schedule.py +
    tests/test_scheduler_cli.py
  - [x] VZ-2: `--jql` passthrough in `worklog_visualizer/` + appends to
    tests/test_visualizer.py
  - [x] HYG-1..4: .github/workflows/ci.yml, .pre-commit-config.yaml,
    .gitleaks.toml, .gitignore credential patterns
- [x] **Phase 2 — integration (me, after Phase 1 merges)**: LIB-2 (wire
  on_progress in both tools), LIB-10 (third canonical export shape, opt-in in
  both tools), console-script entries in pyproject.toml for new packages.
- [x] **Phase 3 — probe live run**: run executed outside this environment on
  2026-07-28 (the sandbox has neither the PAT nor egress to the host) and its
  `findings.json` handed back. Folded into kb/trackspace.json as ONE cited edit
  (the probe never writes its own KB entries). KBW-1..5 closed; **KBW-6
  (rate limiting) deliberately still open** — see NT-1 findings below.
- [x] **Phase 4 — gate + docs + ship**: full pytest/ruff/mypy/bandit, README +
  CLAUDE.md updates, conventional commits, push to main.
- [x] HYG-5: report-only, findings recorded below. No git history action.

## Design decisions

- **KB endpoint entries for unprobed endpoints**: paths/methods/params are safe
  static knowledge (public Jira DC REST v2 surface, per the audit); the
  *response shapes* in their fixtures are synthetic placeholders until a probe
  run captures real ones. Each new entry's `source` says exactly that.
- **test_kb.py method whitelist**: extended from {GET, POST} to include
  PUT/DELETE because the companion package needs comment-update /
  link-delete endpoints. This is a KB sanity meta-test, not a pinned behaviour
  assertion; extending the allowed set is additive.
- **DELETE/no-body endpoints**: share a `no_content.json` fixture (`{}`) since
  test_kb.py requires every endpoint to name an existing fixture; Jira answers
  these with 204 and an empty body, which `request_json` already maps to None.
- **Attachment binary download (GET /attachment/content/{id})**: deliberately
  NOT built — `request_json` is JSON-only and a binary path is new client
  surface beyond the audit entries' scope. Named in the final report as
  adjacent work, per the scope fence.
- **Transition execute (POST .../transitions)**: KB entry added up front (safe
  static surface) with no tool code until the gate opened. The 2026-07-28 probe
  run returned a real transition graph, so the execute path was then built —
  never retried, validated against a freshly fetched list, confirmed unless
  `--yes`. See the NT-1 findings section.
- **ICS (SC-9 export / SC-10 import)**: minimal hand-rolled VEVENT
  writer/parser on stdlib, no new dependency. Only DTSTART/DTEND/SUMMARY are
  honoured; good enough for calendar-import seeds, documented in help text.

## Lessons per item

- **NT-1**: the rate-limit step deliberately bypasses `request_json` (raw
  `client.session.request`) so it can capture headers/elapsed even on 200s with
  retries never engaged — a documented exception to "everything through the
  client", scoped to that one step. The GET-only startup guard doubles as a
  regression tripwire if someone later points a step at a write endpoint.
- **NT-2..6**: merging five audit entries into one package worked because they
  share everything: one issue in hand, one chrome, one fixture set. The one
  cross-agent hiccup: the package started life as `companion.py` and renamed
  itself to `cli.py` to match the entry point pyproject had staged — naming the
  console-script module up front would have avoided it.
- **SC-1**: the audit's framing held exactly — dropping `paginate=False` was a
  one-argument change because the paginating path already existed and was
  already proven by the visualiser. The real work was saying out loud that
  totals can now increase (docstring + call-site note), not the code.
- **SC-7**: `skip_dates` on the meeting, checked inside `occurs_on`, needed no
  grammar change — config-field + tolerant `from_dict` was enough.
- **SC-8/9/10**: one shared hand-rolled ICS module (`worklog_scheduler/ics.py`)
  covers export, import and exclusion files on stdlib alone. All-day VEVENTs
  are skipped with a warning; `apply_flags` now returns warnings so they can
  print once a console exists (small signature change, single caller).
- **SC-17**: the safe pattern was widening ONLY the duration capture group with
  longest-alternative-first ordering (`\d+h\d+m|\d+h|\d+m|\d+`) and
  converting in a helper — the full pre-existing negative-case battery ran
  green before any new tests were written. Riskiest item, landed last on a
  green base, as planned.
- **SC-18/19**: strictly opt-in panels/columns; the pinned default outputs
  never changed. `WorklogEntry.source` defaults to `""` and nothing pinned
  compares entry equality, so the extra field was free.
- **VZ-2**: composing `(EXPR) AND worklogDate ...` and keeping the client-side
  author/instant filters is the whole feature — arbitrary JQL selects issues,
  not worklogs (quirks #3/#4). What arbitrary JQL does on the real instance
  remains unverified ground.
- **LIB-2**: the callback existed since day one; wiring it was ~15 lines across
  both tools. The lesson is that unused seams rot silently — nothing had ever
  exercised `on_progress` outside client tests.
- **LIB-10**: reconciliation by *third shape* (issue/summary/date/hours/
  comment/author, empty strings for uncollected fields, schema marker
  `trackspace-worklog-rows/1`) let both pinned export tests stay untouched.
  `read_canonical` exists so future replay work starts from a validated shape.
- **HYG-1**: the first real CI runs caught three real bugs, one per gate step
  they unblocked, none reproducible in the build sandbox:
  1. the `types-python-dateutil` pin named a version that was never published
     (written from memory instead of `pip list`) — fixed in a01fd02. Pins go
     in from `pip list --format=freeze`, never typed by hand.
  2. `pip-audit` audits the whole environment, and the hosted runner ships a
     setuptools old enough to carry PYSEC-2026-3447 — fixed in a7e068f by
     upgrading pip/setuptools in the install step (at the source, not by
     ignoring the advisory).
  3. test modules import helpers as `tests.conftest`, which only resolves with
     the repo root on sys.path; `python -m pytest` adds it implicitly, bare
     `pytest` (what CI runs) does not — fixed in f88c9fe with pytest's
     `pythonpath = ["."]` so both invocation styles work.
  The pipeline went green end-to-end at f88c9fe (run 30298050553).
- **HYG-2/3/4**: config-only; pre-commit/gitleaks binaries aren't in this
  sandbox, so those run unverified until someone executes them locally or the
  gitleaks hook fires in pre-commit.

## HYG-5 — instance-identifying exposure (report only, no action taken)

From the audit's hygiene pass (full grep + `git log --all -S` pickaxe):
- `trackspace.lhsystems.com`, `CLOPSSEC`, `adam.papp`, `lhsystems.int` appear
  across dozens of files (docs, KB, fixtures, tests) and were all introduced in
  the repo's 2nd/3rd commit — the foundational "Build the Trackspace tooling
  monorepo" commit pair (`cd8d096`/`d8d0836`), which everything since builds on.
- `adam-gabor.papp` never appears anywhere in tree or history.
- No real secret exists anywhere (all token-adjacent values are docs or
  explicitly fake test values like `test-token`/`s3cr3t`); no credential file
  was ever committed.
- Removing the identifying strings would require a git history rewrite
  (`git filter-repo`-class), since they are load-bearing in nearly every
  commit. **No action taken; that decision belongs to the repo owner.**

## NT-1 findings — the live run happened (2026-07-28)

The 2026-07-27 attempt from the build sandbox failed twice over: `TRACKSPACE_PAT`
was not set, and independently `curl https://trackspace.lhsystems.com/...` was
refused by the egress proxy (`CONNECT tunnel failed, response 403`) — no request
ever reached Trackspace. The run was then executed **outside** this environment
and its `--export findings.json` handed back for fold-in.

Six of seven default steps succeeded; the seventh is a finding in its own right.

| Step | Result |
| --- | --- |
| fields | 1030 fields — 42 system, 988 custom |
| project | `CLOPSSEC` = **CloudOps Security**, 7 issue types |
| createmeta | **failed** — 404 `Issue Does Not Exist` (quirk #18) |
| statuses | 183 global statuses across all three categories |
| transitions | 1 available from CLOPSSEC-41456: `831` Reopen → Open |
| permissions | 69 granted / 18 denied; `TRANSITION_ISSUES` granted, `ADMINISTER` denied |
| error-shape | 404 body witnessed; bogus and malformed keys are indistinguishable |

The opt-in `rate-limit` burst was **not** run, so KBW-6 stays open — see below.

### What was folded in, and where

One cited edit to `kb/trackspace.json`, citing
`probe run 2026-07-28 (instance_probe/trackspace-probe --export; catalogues in
kb/probe-catalogues.json)` on every fact:

- **KBW-1 custom field ids** — closed. `fields.custom` deliberately stays empty
  (no tool consumes one yet); `fields.custom_catalogue` records the count and
  points at the catalogue. Copy a field in *with its provenance* the first time
  a tool needs it, rather than hardcoding an id at the call site.
- **KBW-2 issue types** — closed. All 7 `CLOPSSEC` types inline in
  `fields.issue_types`. Other projects were not probed and stay unknown.
- **KBW-3 workflow states** — closed. New top-level `workflow` key: status count
  + catalogue pointer, the observed transition sample, and the permission
  summary. The note is explicit that a status catalogue is not a workflow graph.
- **KBW-4 CLOPSSEC's real name** — closed. `CloudOps Security` (my placeholder
  guessed "Cloud Ops Security" — one space wrong, which is exactly why guesses
  do not belong in the KB).
- **KBW-5 error body shape** — closed and upgraded from `inferred` to
  **witnessed**, with the verbatim 404 body and the indistinguishability finding
  (quirk #19).
- **KBW-6 rate limiting** — **still open, deliberately.** The burst step is
  opt-in and was not run, so nothing has ever observed a 429 from this instance.
  The KB now says so in as many words, including "Do not record an absence of
  rate limiting — nothing has tested for it." A test pins that sentence.

The bulky catalogues (988 custom fields, 183 statuses, 87 permissions) live in
`kb/probe-catalogues.json` so `trackspace.json` stays readable; the raw
`findings.json` was not committed verbatim.

### Fixtures: placeholders replaced by witnessed shapes

`field_list`, `project_CLOPSSEC`, `status_list`, `issue_transitions_*`,
`mypermissions_*` and `errors.json`'s 404 now carry real data. Six test
assertions moved with them (project name, issue-type count, status names,
transition ids, custom field ids) — those pinned *fixture contents*, not tool
behaviour, so updating them is the correct response. My three invented custom
fields (`customfield_10001/2/3` "Team"/"Story Points"/"Severity") were deleted
rather than kept alongside the real ones.

`issue_createmeta_CLOPSSEC.json` is kept but reclassified: it is the success
shape of an endpoint this instance does not serve, retained only so the probe's
parsing stays covered.

### Transition execute — gate satisfied, path built

The sequencing rule was "no execute path until a probe run records a real
transition graph". It did, so `execute_transition` now exists on the client and
`issue_companion transitions --to <id|name>` drives it. Three properties worth
keeping:

- **Never retried.** Unlike the worklog POST, whose replay double-books, a
  replayed transition either fails (the id is invalid from the new status) or
  moves the issue twice through a looping workflow. Same rule, different reason.
- **The live list is the validation set.** The command re-fetches transitions
  and refuses anything absent from that fresh set, because the available set is
  a snapshot of the current status (quirk #20). Ids are matched before names.
- **Confirmed unless `--yes`**, like every other mutation in that tool.

### What is still open after this run

- **KBW-6 / rate limiting** — needs `trackspace-probe --only rate-limit
  --rate-limit-burst N` from a machine with network line-of-sight. Report it as
  "0/N 429s at burst=N on <date>", never as "no rate limit".
- **The createmeta successor path** — `/issue/createmeta/{projectIdOrKey}/issuetypes`
  is unprobed. Adding it means a KB entry, a fixture and a probe step.
- **Other projects' issue types and workflows** — only `CLOPSSEC` was probed.
