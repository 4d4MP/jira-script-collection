# Trackspace tools

Internal Python 3 tooling for **Trackspace** — our Jira Data Center instance at
`https://trackspace.lhsystems.com`. Every tool shares one HTTP client, one
knowledge base of instance facts, and one CLI look.

"Jira" appears in this repo only where it names the underlying REST API. The
instance is Trackspace.

```
kb/                    facts about the instance + offline fixtures
trackspace/            shared library: client, auth, errors, CLI UX
worklog_scheduler/     plan, preview and post meeting worklogs
worklog_visualizer/    show what you logged over a window
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

Configuration lives at `~/.jira_worklog_manager.json`, the original path, so
existing configs keep working.

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

Exit codes: `0` fine · `1` request failed · `2` auth or configuration problem ·
`130` cancelled with Ctrl+C.

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
