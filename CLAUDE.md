# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Python 3.11+ tooling for **Trackspace**, our Jira Data Center instance at
`https://trackspace.lhsystems.com`. Use "Trackspace" in all user-facing text —
"Jira" only when naming the underlying REST API.

There is no test instance and no network access to production. Static analysis
and fixture-backed tests are the only quality gate.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

pytest                                            # no network, no PAT
pytest tests/test_client.py::test_myself          # single test
pytest -k pagination                              # by name

ruff check . && ruff format --check .
mypy                                              # strict; paths come from pyproject
bandit -c pyproject.toml -r .
pip-audit
```

`TRACKSPACE_PAT` is read from the environment for real runs; tests never need it.
Run tools from the repo root (`python -m worklog_scheduler`, `python -m
worklog_visualizer`) or install and use the `trackspace-worklog-*` console
scripts.

## Architecture

### `/kb` is the source of truth, not documentation

Every fact about the instance — endpoints, timeouts, page sizes, JQL templates,
field ids, defaults, the `started` wire format — lives in `kb/trackspace.json`
and reaches code through `trackspace.kb.KnowledgeBase`. **A literal about
Trackspace appearing in a tool is a bug.** If a fact is missing, add it to the
KB with a `source` (a `work_log.py:240`-style line reference into the two
original scripts) or the string `inferred`. Markdown files in `/kb` are the
human-readable companion; `kb/quirks.md` explains the load-bearing oddities and
should be read before changing anything that touches the API.

Things deliberately recorded as *unknown* rather than guessed: custom field ids
(`fields.custom` is empty because neither original script used one), issue types,
workflow states, and rate-limit behaviour. Do not invent them.

`kb/fixtures/` holds a synthetic but shape-accurate response for every endpoint,
including error, empty and malformed cases. The whole test suite runs off these.

### `/trackspace` — shared library

* `client.py` — `TrackspaceClient`. Pages by **returned count**, never by
  requested page size, so a server that caps `maxResults` cannot cause skipped
  rows. Retries idempotent GETs on 429/5xx/transport errors and **never retries a
  worklog POST**: posting is not idempotent and nothing dedupes, so a retry of a
  request that landed double-books time.
* `errors.py` — `str(ApiError)` is `HTTP {status}: {body[:200]}`, matching what
  the original tool printed for a failed submit. Tools reproduce that line.
* `auth.py` — PAT from `TRACKSPACE_PAT` (falling back to `JIRA_API_TOKEN`).
  `Credentials.__repr__` is redacted; nothing ever renders the token.
* `ui/` — the entire CLI look: `theme` (fixed colours/glyphs, `NO_COLOR` and
  dumb-terminal degradation), `chrome` (boxed header, `LiveStatus`, `cancellable`
  for Ctrl+C → exit 130 with a summary, outcome-first `final`), `tables`
  (truncate, never wrap), `charts` (Unicode bars, terminal-only), `prompts`
  (questionary via `unsafe_ask()` so Ctrl+C propagates). Build CLIs from these
  rather than hand-rolling, so the two tools cannot drift apart.

### The two tools

`worklog_scheduler/` was rewritten from a Tkinter GUI into an interactive CLI,
but its **behaviour is frozen**: same schedule expansion, dry-run default, same
posting semantics, same dashboard figures, same config file at
`~/.jira_worklog_manager.json` with the historical `jira_base` key. Split as
config → schedule (expansion + flag-spec parsing) → dashboard (fetch, aggregate,
render) → entry point.

`worklog_visualizer/` was rewritten the same way — interactive by default,
terminal-first rendering — while keeping what it *computes*: window parsing,
author filtering, the summary figures, and the matplotlib figure itself (now in
`figure.py`, reached only on image export). Split as window → fetch → terminal /
figure → entry point. Its one-shot form is `report`, and bare `--ago`/`--date`
flags still work without the subcommand.

Two behaviours changed deliberately in that rewrite and are worth not undoing: a
file is written only when `--export` (or legacy `--output`) names one, and a
malformed `started` is skipped with a warning instead of ending the run. The two
tools still have different IP-normalising regexes on purpose (`kb/quirks.md`
#13).

### CLI contract for anything new

Interactive with no arguments *and* a flag for every interactive choice; boxed
header with tool, instance and auth presence; live status naming the current
operation with counts; dense bordered tables with truncation; charts rendered in
the terminal, with a file written only when an explicit `--export` flag is passed
(and the terminal rendering still printed alongside it).

Scheduler exit codes: `0` ok, `1` some worklogs failed, `2` configuration
problem, `130` cancelled.

### Tests

`tests/conftest.py` provides `FakeSession` plus `fixture_router(kb, ...)`, which
answers requests from `kb/fixtures` and simulates a server capping pages at 2
rows. Use `make_client(kb, handler)` for client-level tests and
`patch_client(monkeypatch, kb)` (in `test_scheduler_cli.py` and
`test_visualizer.py`) for end-to-end CLI runs. Never add a test that touches the network.

Preserved behaviours are pinned by assertions on exact strings (`"HTTP 400"`,
`"Posted 6/7 worklogs"`, `"2026-04-01T10:00:00.000+0200"`). If one fails, the
question is whether behaviour changed, not whether the assertion is too strict.
