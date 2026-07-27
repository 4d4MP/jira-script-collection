# Build notes — capability-audit subset build

Running notes for the scoped build of: NT-1 (+KBW-1..6), NT-2..NT-6 (merged into
one package), SC-1, SC-7, SC-8, SC-9, SC-10, SC-17, SC-18, SC-19, VZ-2, LIB-2,
LIB-10, HYG-1..5. One lesson per item as it lands. Specs live in
`kb/proposals/capability-audit.md` under each ID.

## Orchestration state (updated as phases complete)

- [ ] **Phase 0 — foundation (single-writer choke points)**: new KB endpoint
  entries in `kb/trackspace.json` (probe + companion endpoints, paths/methods
  only — response shapes stay unverified until probed), all new fixtures in
  `kb/fixtures/`, routing in `tests/conftest.py`, rows in
  `kb/fixtures/README.md`. `tests/test_kb.py`'s method whitelist extended to
  GET/POST/PUT/DELETE (meta-test, not pinned behaviour). Commit.
- [ ] **Phase 1 — parallel packages** (agents; none may touch
  kb/trackspace.json, tests/conftest.py, kb/fixtures/*, README.md, CLAUDE.md):
  - [ ] NT-1: `instance_probe/` package + `tests/test_probe.py`
  - [ ] NT-2..6: `issue_companion/` package + `tests/test_issue_companion.py`
    (+ multipart/headers support in `trackspace/client.py` — that agent owns
    client.py). Transition *execute* path NOT built (gated on a real probe
    run returning a transition graph).
  - [ ] SCHED: SC-1, SC-7, SC-8, SC-9, SC-10, SC-17, SC-18, SC-19 in
    `worklog_scheduler/` + appends to tests/test_schedule.py +
    tests/test_scheduler_cli.py
  - [ ] VZ-2: `--jql` passthrough in `worklog_visualizer/` + appends to
    tests/test_visualizer.py
  - [ ] HYG-1..4: .github/workflows/ci.yml, .pre-commit-config.yaml,
    .gitleaks.toml, .gitignore credential patterns
- [ ] **Phase 2 — integration (me, after Phase 1 merges)**: LIB-2 (wire
  on_progress in both tools), LIB-10 (third canonical export shape, opt-in in
  both tools), console-script entries in pyproject.toml for new packages.
- [ ] **Phase 3 — probe live run**: attempt NT-1 against production with
  TRACKSPACE_PAT (network may be blocked by the sandbox proxy — report
  honestly either way). Fold findings into kb/trackspace.json as ONE deliberate
  cited edit (probe never writes its own KB entries). KBW-1..6.
- [ ] **Phase 4 — gate + docs + ship**: full pytest/ruff/mypy/bandit, README +
  CLAUDE.md updates, conventional commits, push to main.
- [ ] HYG-5: report-only, findings recorded below. No git history action.

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

(filled in as each item lands)

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

## NT-1 findings (filled in after the live probe run)

(pending)
