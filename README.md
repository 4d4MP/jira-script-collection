# Trackspace tools

Internal Python 3 tooling for **Trackspace** — our Jira Data Center instance at
`https://trackspace.lhsystems.com`. Every tool shares one HTTP client, one
knowledge base of instance facts, and one CLI look.

"Jira" appears in this repo only where it names the underlying REST API. The
instance is Trackspace.

```
kb/                    facts about the instance + offline fixtures
trackspace/            shared library: client, auth, errors, export, CLI UX
worklog_scheduler/     plan, preview and post meeting worklogs
worklog_visualizer/    show what you logged over a window
instance_probe/        read-only probe that resolves the KB's unknowns
issue_companion/       transitions, comments, attachments, links, changelog
tests/                 runs entirely against kb/fixtures — no network, no PAT
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Auth comes from the environment and stays there:

```bash
export TRACKSPACE_PAT="..."     # generate at /secure/ViewProfile.jspa
```

No tool ever prompts for the token, writes it to disk, or prints it. Headers say
*present* or *missing* and nothing more.

## The tools

### `worklog_scheduler` — plan and post meeting worklogs

Books recurring and one-off meeting time against a Trackspace issue over a date
range, and shows what has been logged.

```bash
python -m worklog_scheduler                                  # interactive session
python -m worklog_scheduler preview --range this-month
python -m worklog_scheduler submit --live --yes
python -m worklog_scheduler dashboard --from 2026-04-01 --to 2026-04-30
python -m worklog_scheduler config --export ./schedule.json
```

Dry run is the default: `submit` prints what it *would* post until you pass
`--live`. Every interactive editor has a flag equivalent —
`--recurring MON-FRI@10:00+30=Daily`, `--oneoff 2026-04-03@13:00+30=Workshop`,
`--exclude 2026-04-02` — so the same tool runs in CI.

Recurring meetings can repeat every week or every N weeks:

```
--recurring "TUE@14:00+60=Weekly sync"                    # every Tuesday
--recurring "TUE/2@14:00+60=Bi-weekly sync"               # every other Tuesday
--recurring "TUE/2~2026-07-21@14:00+60=Bi-weekly sync"    # …counting from that week
```

`/N` sets the interval and `~YYYY-MM-DD` names a week it definitely happens in.
Without an anchor the counting starts from the first week of the range, so pin
one if the range is going to move.

In the interactive session, ← goes back a level — out of a submenu, or out of a
half-finished meeting — and Ctrl+C leaves cleanly from anywhere.

Configuration lives at `~/.jira_worklog_manager.json`, the original path, so
existing configs keep working.

Recent additions, all opt-in: per-meeting `skip_dates` (cancel one meeting on
one date without excluding the whole day), `--exclude-file PATH` (plain-text or
`.ics` holiday lists), `preview --export PATH` (`.csv`/`.json`/`.ics` of the
planned schedule — the terminal preview still prints), `--import-ics PATH`
(VEVENTs become one-off meetings; all-day events are skipped with a warning),
`+1h30m`/`+2h`/`+45m` duration sugar in meeting specs (plain digits still mean
minutes), `preview --explain` (which rule produced each row), and
`dashboard --target-hours-per-day N` (a separate target-vs-actual panel).

One deliberate behaviour change: the dashboard now paginates each issue's
worklogs fully instead of reading only the first page, so totals can
legitimately increase for issues that were silently truncated before.

Exit codes: `0` fine · `1` some worklogs failed · `2` configuration problem ·
`130` cancelled with Ctrl+C.

### `worklog_visualizer` — what you logged over a time window

```bash
python -m worklog_visualizer                                 # interactive session
python -m worklog_visualizer report --ago 7d
python -m worklog_visualizer report --date 2026_04_01-2026_04_30
python -m worklog_visualizer report --ago 4M --user colleague.name
python -m worklog_visualizer report --ago 1Y --export quarter.png
```

The report renders in the terminal: hours stacked by ticket across the timeline
(bucketed into weeks, then months, as the window grows), a daily sparkline, the
top tickets grouped by title with IP addresses collapsed, a per-ticket table, and
the summary figures.

A file is written **only** when `--export` names one — `.png` / `.pdf` / `.svg`
for the multi-panel matplotlib image, `.json` / `.csv` for the rows behind it —
and the terminal report still prints alongside it. `--show` additionally opens
the image in a window. The old `--output PATH` spelling still exports, and
`--no-show` is accepted and ignored (nothing opens unless you ask for it).

Windows: `--ago 5m|10h|2d|4M|1Y` (case-sensitive units), `--date` as
`YYYY_MM_DD-YYYY_MM_DD` or `YYYYMMDD-YYYYMMDD`, or `--datetime` down to the
minute. Absolute values are read in local time; `worklogDate` is day-granular, so
sub-day windows are filtered client-side.

`--jql "EXPR"` replaces the author clause as the issue-selection filter (the
window bounds are still ANDed on, and whose worklogs are counted is still
`--user`'s job). Anything beyond the two proven JQL templates is unverified
ground on this instance.

Exit codes: `0` fine · `1` request failed · `2` auth or configuration problem ·
`130` cancelled with Ctrl+C.

### Canonical export (both tools)

`--export-canonical PATH` (`.json`/`.csv`) writes the shared row shape
`issue, summary, date, hours, comment, author` (schema
`trackspace-worklog-rows/1`, defined in `trackspace/export.py`). Fields a tool
does not collect are empty strings, so every canonical file has the same
columns whichever tool wrote it. The historical per-tool export formats are
unchanged.

### `instance_probe` — turn the KB's unknowns into sourced facts

```bash
python -m instance_probe                       # all read-only steps
python -m instance_probe --only fields --only project
python -m instance_probe --rate-limit-burst 5  # opt-in, hard-capped at 20
python -m instance_probe --export findings.json
```

GET-only by construction (a startup guard refuses any non-GET step). Steps:
fields, project, createmeta, statuses, transitions (list only), permissions,
error-shape (deliberate 404/400 against bogus keys), and the opt-in rate-limit
burst, reported strictly as "0/N requests returned 429 at burst=N on <date>".
The probe **never writes `kb/trackspace.json`** — findings are folded in by a
human, with the probe run cited as the fact's `source`.

A run on **2026-07-28** closed most of the KB's declared unknowns: `CLOPSSEC` is
"CloudOps Security", 988 custom fields and 183 statuses were catalogued into
`kb/probe-catalogues.json`, and the 404 error body is now witnessed rather than
inferred. Two things did not close: the `rate-limit` step is opt-in and was not
run (so **nothing has ever observed a 429 from this instance** — the KB says so
explicitly), and `GET /issue/createmeta` turned out to 404 on this instance
(`kb/quirks.md` #18). See `kb/build-notes.md` for the full fold-in.

### `issue_companion` — everything around one issue

```bash
python -m issue_companion                              # interactive
python -m issue_companion show CLOPSSEC-41456 --changelog --attachments --links
python -m issue_companion transitions CLOPSSEC-41456   # list
python -m issue_companion transitions CLOPSSEC-41456 --to Reopen --yes
python -m issue_companion comment CLOPSSEC-41456 add --body "note"
python -m issue_companion attach CLOPSSEC-41456 upload report.png
python -m issue_companion link CLOPSSEC-41456 add --type Blocks --to CLOPSSEC-41501
```

Comments (list/add/update/delete), attachments (list/upload/delete — upload
preflights `/attachment/meta` and refuses oversized files), issue and remote
links (type names validated against the instance's link types), and the
changelog. Mutations confirm interactively unless `--yes`.

Executing a transition (`--to <id or name>`) was gated on a probe run recording
a real transition graph; the 2026-07-28 run did, so it now exists. It re-fetches
the transition list first and refuses anything not in that fresh set, because
the available transitions are a snapshot of the issue's *current* status, not a
workflow graph (`kb/quirks.md` #20). Like the worklog POST it is never retried.

## Adding a tool

1. Read `/kb` first. If your tool needs an instance fact, it comes from
   `trackspace.kb`, not from a literal in your code. If the fact is not in the KB,
   add it there with its provenance.
2. Create a package `your_tool/` with `__main__.py` and one module named after
   what the tool does. Register a console script in `pyproject.toml`.
3. Build the CLI from `trackspace.ui` — `chrome.header`, `chrome.LiveStatus`,
   `chrome.cancellable`, `chrome.final`, `tables.render_table`, `charts.*`,
   `prompts.*`. Do not hand-roll spinners, colours or box drawing: the look is
   shared so it cannot drift.
4. Follow the CLI contract:
   * interactive when launched with no arguments, with a flag for every choice;
   * a boxed header naming the tool, the instance and auth presence;
   * a live status line naming the current operation and its counts;
   * fixed colours and glyphs for success / warning / error / info;
   * dense bordered tables, long values truncated with a marker, never wrapped;
   * charts rendered in the terminal — a file is written only when an explicit
     `--export` flag is passed, and the terminal rendering still prints;
   * ← goes back a level in any menu (`prompts.select(..., allow_back=True)`);
   * Ctrl+C exits cleanly with a summary of what completed;
   * `NO_COLOR` and dumb terminals degrade to plain ASCII.
5. Write tests against `kb/fixtures`. No test may touch the network.

## Quality gate

There is no test instance to run against, so static analysis is the gate. All of
these must be clean:

```bash
ruff check . && ruff format --check .
mypy                      # strict, over the library and both tool entry points
bandit -c pyproject.toml -r .
pip-audit
pytest
```

Do not silence a finding you can fix. Where a suppression is genuinely right, it
carries a one-line justification at the point of use.

## What the tools were before

| Original | Now |
| --- | --- |
| `work_log.py` — Tkinter worklog manager + matplotlib dashboard | `worklog_scheduler/schedule_and_post_worklogs.py` |
| `work.py` (self-titled `visualize_jira_worklogs.py`) | `worklog_visualizer/visualize_logged_worklogs.py` |

The scheduler was rewritten from a desktop GUI into a terminal CLI, and the
visualiser from a PNG-first script into an interactive one. What each tool
*computes* is unchanged in both cases: the same schedule expansion, dry-run
default and posting semantics; the same window parsing, author filtering and
summary figures; the same matplotlib figure when you ask for an image.
`kb/quirks.md` records the things that look odd and are load-bearing.
