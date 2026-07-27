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
    client.py). Transition *execute* path NOT built (gated on a real probe
    run returning a transition graph).
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
- [~] **Phase 3 (tool built; live run NOT possible from this environment — see NT-1 findings below) — probe live run**: attempt NT-1 against production with
  TRACKSPACE_PAT (network may be blocked by the sandbox proxy — report
  honestly either way). Fold findings into kb/trackspace.json as ONE deliberate
  cited edit (probe never writes its own KB entries). KBW-1..6.
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
- **Transition execute (POST .../transitions)**: KB entry added (safe static
  surface) but no tool code and no tests for the execute half until a probe
  run returns a real transition graph — per the sequencing constraint.
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

## NT-1 findings — live run NOT possible from this environment (2026-07-27)

Attempted after the tool was built and its offline tests passed:

- `TRACKSPACE_PAT` is **not set** in this session's environment (contrary to
  the task brief's expectation). The probe refused with the standard
  ConfigurationError, exactly as designed.
- Independently of auth, the instance is **unreachable from this sandbox**:
  `curl https://trackspace.lhsystems.com/rest/api/2/serverInfo` fails with
  `CONNECT tunnel failed, response 403` from the egress proxy. No request
  reached Trackspace.

Consequences, stated per the sequencing rules:
- **KBW-1..6 remain open.** kb/trackspace.json's unknowns (custom field ids,
  issue types, workflow states, CLOPSSEC's real name, error body shape,
  rate-limit behaviour) are left exactly as they were — no fold-in happened,
  because there are no findings to fold in. The seven probe fixtures remain
  synthetic placeholders, flagged as such in kb/fixtures/README.md.
- **The transition-execute path stays unbuilt** (its gate — a real probed
  transition graph — was never satisfied).
- To close this out: run `trackspace-probe --export findings.json` from a
  machine with the PAT and network line-of-sight to the instance, then fold
  the findings into kb/trackspace.json as one cited edit ("probe run <date>,
  see <report path>") — the probe deliberately never writes the KB itself.
