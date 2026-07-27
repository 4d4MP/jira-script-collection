# Pagination, errors, timeouts

## Pagination

Classic Jira Data Center offset pagination. Request `startAt` and `maxResults`;
the response echoes both and adds `total` plus the payload array (`issues` for
search, `worklogs` for the worklog endpoint).

No cursor or `nextPageToken` pagination was observed anywhere. If a response ever
arrives without `total`, the visualiser's `data.get("total", 0)` terminates the
loop after one page rather than crashing — a deliberate soft landing.

### Page sizes

| Endpoint | Page size | Source |
| --- | --- | --- |
| `/search` | 100 | `work_log.py:284`, `work.py:306` |
| `/issue/{key}/worklog` | 1000 (visualiser); scheduler sends none and takes the server default | `work.py:311`, `work_log.py:306-308` |

### Two different advance strategies — both preserved

* **By returned count** — `startAt += len(page)`, stop when
  `startAt + len(page) >= total` (`work_log.py:290-293`). Correct even if the
  server silently caps `maxResults` below what was requested.
* **By requested page size** — `startAt += page_size`, stop when
  `startAt + page_size >= total` (`work.py:315-319,330-337`). Skips rows if the
  server ever returns a short page. It is proven against this instance, so the
  visualiser keeps it; the shared client's own pager uses the returned-count
  strategy because it is strictly safer.

`maxResults=1000` on the worklog endpoint is above Jira's usual default and may
be capped server-side. Whether this instance honours it is **unconfirmed** — but
it has not caused missing data in practice.

## Errors

### Handling that exists today

| Status | Behaviour | Source |
| --- | --- | --- |
| 401 | Visualiser exits with `Auth failed (401). Check TRACKSPACE_PAT.` | `work.py:275-276` |
| 403 | Visualiser exits with `Forbidden (403). Token lacks permission for this resource.` | `work.py:277-278` |
| other 4xx/5xx | `raise_for_status()` | `work.py:279`, `work_log.py:254,288,309` |
| any non-2xx on worklog POST | Reported as `HTTP {status}: {body[:200]}`, counted as a failure, run continues | `work_log.py:247-249,1310-1316` |
| exception fetching one issue's worklogs | Logged as `  failed {key}: {e}`, remaining issues still processed | `work_log.py:335-337` |

### Response body shape

Jira Data Center returns `{"errorMessages": [...], "errors": {...}}`. **Inferred**
— neither script parses it. The only confirmed fact is that the scheduler shows
the first 200 characters of the raw body verbatim, which is what the shared
client's error messages reproduce.

### Rate limiting

**Unconfirmed.** No 429 was ever observed and neither script handles one. The
shared client treats 429 as retryable, honours a `Retry-After` header when
present, and otherwise backs off exponentially. That policy is *inferred*, not
witnessed.

### Retry policy in the shared client

Retries apply to idempotent GETs only — 429, 5xx, and transport errors
(timeout / connection reset), with exponential backoff. `POST .../worklog` is
**never** retried automatically, because a retried worklog that actually landed
the first time silently double-books time. See `quirks.md`.

## Timeouts

| Call | Scheduler | Visualiser |
| --- | --- | --- |
| `/myself` | 30 s | 30 s |
| `/search` | 60 s | 30 s |
| `/issue/{key}/worklog` | 60 s | 30 s |
| `POST .../worklog` | 30 s | — |

Source: `work_log.py:246,253,286,307`; `work.py:274`. The scheduler's longer
search timeout reflects that a `worklogAuthor` search over a month is slow on
this instance.
