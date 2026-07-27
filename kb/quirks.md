# Instance quirks and gotchas

Things the working scripts do that look wrong at a glance and are not. Changing
any of these without evidence from production is how this tooling breaks.

### 1. `started` is formatted `%Y-%m-%dT%H:%M:%S.000%z`

`2026-04-01T10:00:00.000+0200` — milliseconds always `.000`, offset with **no**
colon. Jira Data Center rejects the colon-separated offset that
`datetime.isoformat()` produces. (`work_log.py:239`)

### 2. `comment` is posted as a bare string

API v2. Cloud's v3 would need Atlassian Document Format; sending ADF here would
store a JSON blob as the comment text. (`work_log.py:244`)

### 3. Authors are filtered client-side even though the JQL filters by author

`worklogAuthor` selects **issues**. The per-issue worklog endpoint then returns
every author's entries on those issues, so without the client-side pass you would
count your colleagues' time as your own. (`work_log.py:311-314`, `work.py:404-411`)

### 4. Dates are filtered client-side too

`worklogDate` is day-granular, and the returned worklogs are not restricted to the
searched window at all. Both scripts re-check `start <= wl_date <= end` after
fetching. (`work_log.py:326-327`, `work.py:418`)

### 5. Author matching accepts both `name` and `key`

A Data Center rename leaves `key` pointing at the old value. Matching either
avoids silently losing history. The visualiser additionally lowercases and accepts
`accountId`/`emailAddress` so the same code would survive a Cloud migration.
(`work_log.py:264-266`, `work.py:404-411`)

### 6. `started` parsing has a strptime fallback

The reader tries `fromisoformat` after swapping `Z` for `+00:00`, then falls back
to `strptime(raw[:19])`, which drops the offset and yields a naive datetime. On
modern Python the fallback almost never fires — it dates from when
`fromisoformat` could not read `+0200`. Harmless, and cheap insurance against an
unexpected `started` shape. (`work_log.py:315-324`)

### 7. `startedAfter` is UTC epoch **milliseconds**

Not seconds. `int(start_dt.timestamp() * 1000)`. (`work.py:352`)

### 8. The scheduler reads only the first page of an issue's worklogs

`GET /issue/{key}/worklog` with no `startAt`/`maxResults`. For the meeting issue
this is fine; on a very heavily logged issue it would truncate. Deliberate
simplicity, recorded here as a known bound. (`work_log.py:306-308`)

### 9. "Auto-skips weekends" is emergent, not enforced

Nothing in the entry builder excludes Saturday and Sunday. Weekends stay empty
only because the default recurring meetings are `Mon–Fri`. A recurring meeting
configured for Saturday, or a one-off dated on a weekend, **is** logged — and that
is correct behaviour, not a bug to fix. (`work_log.py:190-214`)

### 10. Excluded dates suppress one-offs as well as recurring entries

An exclusion is "I was not working that day", so it beats an explicitly added
one-off. (`work_log.py:206`)

### 11. One-offs outside the date range are silently dropped

They stay in the config — the range is a filter, not a delete. Widen the range and
they come back. (`work_log.py:206`)

### 12. Posting worklogs is not idempotent and has no dedupe

Re-running a submit posts the entries again. The original warns "cannot be undone
via this script". Hence: no automatic retry on `POST .../worklog`, and dry-run is
the default. (`work_log.py:1273-1281`, `work_log.py:134`)

### 13. The two IP normalisers are deliberately different

Both collapse addresses in alert titles so `Suspicious login from 10.0.0.5` and
`… from 192.168.1.10` group into one bar.

* Scheduler: IPv4 only, bare addresses. (`work_log.py:80`)
* Visualiser: IPv4 with optional `/CIDR` and `:port`, plus IPv6 including `::`
  compressed forms, then whitespace collapse. (`work.py:219-251`)

Each tool keeps its own, because grouping is a presentation decision and the two
tools' outputs are compared against their own history.

### 14. Config lives at `~/.jira_worklog_manager.json`

Kept at the original path across the rename so existing configs keep loading.
Unknown keys are dropped on load, so a config written by an older build still
works. (`work_log.py:74,144-147`)

### 15. Neither script retried anything

No backoff, no 429 handling, no connection-error recovery. The shared client adds
this for GETs. Any claim about how this instance rate-limits is **inferred**.

### 16. The two tools disagree about a malformed `started`, on purpose

The scheduler skips an unparseable timestamp, warns, and carries on. The
visualiser lets `datetime.fromisoformat` raise, which ends the run. That is the
original behaviour of each script and it is preserved: the visualiser is a
single-shot report where a corrupt entry should be loud, and it was left
functionally untouched. If Trackspace ever starts emitting a `started` shape
neither reader understands, the visualiser is where you will find out.
(`work.py:416`, `worklog_scheduler/dashboard.py`)

### 17. The visualiser's docstring says `lhsystems.int`, the code says `lhsystems.com`

Every URL in both scripts points at `https://trackspace.lhsystems.com`. The
docstring is loose prose about the hosting domain. Trust the URLs.
