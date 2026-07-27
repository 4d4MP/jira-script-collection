# JQL patterns

Both patterns are proven against this instance. Both answer *which issues carry
worklogs*, never *which worklogs* — that distinction drives the whole two-phase
fetch (search, then per-issue worklog pull, then filter client-side).

## Worklogs by the current user in a date range

```
worklogAuthor = currentUser() AND worklogDate >= "2026-04-01" AND worklogDate <= "2026-04-30"
```

Source: `work_log.py:268-272`, `work.py:384-388`. Requested with
`fields=summary`.

## Worklogs by a named user in a date range

```
worklogAuthor = "adam.papp" AND worklogDate >= "2026-04-01" AND worklogDate <= "2026-04-30"
```

Source: `work.py:363-364,384-388`. The username is not taken from the command
line directly — it is resolved through `GET /user/search` first, and the
canonical `name` (or `accountId`) from that lookup is what goes into the JQL.

## Constraints

* **`worklogDate` is day-granular.** It rejects a time component. A sub-day
  window (`--ago 5m`) must be widened to calendar-day bounds in the JQL and then
  narrowed again client-side against each worklog's `started` instant.
  `work.py:380-388` says so in a comment and implements exactly that.
* **Dates are double-quoted ISO strings**, `"YYYY-MM-DD"`.
* **`worklogAuthor` filters issues, not worklog entries.** An issue matches if the
  author logged *anything* on it in the window; the response then contains that
  issue's worklogs from every author across all time. Client-side filtering by
  author *and* by date is mandatory, not defensive.

## Not observed

No JQL involving project keys, issue types, statuses, sprints, labels or ordering
appears in either script. Any such clause would be new ground against this
instance.
