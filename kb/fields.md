# Fields, projects, issue types, workflow states

## Custom fields — none observed

**No `customfield_*` id appears anywhere in either script.** The `fields.custom`
map in `trackspace.json` is therefore empty, and that means *unknown*, not
*none exist*. The two proven scripts only ever request `summary` and read worklog
sub-objects. Do not invent ids; when a future tool genuinely needs one, add it
here with its provenance.

## System fields in use

| Id | Name | Holds | Source |
| --- | --- | --- | --- |
| `key` | Issue key | `CLOPSSEC-41456`-style identifier. Returned alongside `fields`, not inside it. | `work_log.py:301`, `work.py:400` |
| `summary` | Summary | Issue title. The only field ever requested via `fields=`. | `work_log.py:282`, `work.py:395` |
| `started` | Worklog start instant | `2026-04-01T10:00:00.000+0200` — ISO-8601, millisecond precision, numeric offset **without** a colon. | `work_log.py:239,315-324`, `work.py:413-416` |
| `timeSpentSeconds` | Time spent | Integer seconds. Hours = `/ 3600`. | `work_log.py:242,332`, `work.py:420` |
| `comment` | Worklog comment | Plain string. Missing on some entries — both scripts default it to `""`. | `work_log.py:244,333` |
| `author` | Worklog author | Nested user object; see below. | `work_log.py:311-313`, `work.py:404-410` |

## User identity keys

Data Center identifies users by `name` and `key`; `accountId` is a Cloud concept.
Both scripts match defensively across whatever they find.

| Id | Holds | Used for | Source |
| --- | --- | --- | --- |
| `name` | Login name | Primary author match | `work_log.py:264-266`, `work.py:363` |
| `key` | Immutable user key | Second author match — can differ from `name` after a rename | `work_log.py:264-266`, `work.py:368` |
| `displayName` | Human-readable name | Report headings only | `work_log.py:1362`, `work.py:374` |
| `emailAddress` | Email | Fallback identity key and label (visualiser only) | `work.py:369,375` |
| `accountId` | Cloud account id | Read defensively; expected to be absent here | `work.py:369` |

The two scripts differ in how strictly they match, and both are correct for their
own purposes:

* Scheduler: exact comparison of the author's `name`-or-`key` against the current
  user's `name`-or-`key` (`work_log.py:311-314`).
* Visualiser: lowercased set intersection over
  `{name, key, accountId, emailAddress}` (`work.py:404-411`).

## Projects

| Key | What is known | Source |
| --- | --- | --- |
| `CLOPSSEC` | The project holding `CLOPSSEC-41456`, the issue recurring meeting time is booked against by default. Its human-readable name never appears in either script — **unknown**. | `work_log.py:126` |

Worklog searches are not project-scoped, so results can contain any project the
token can read.

## Issue types and workflow states

**Unknown.** Neither script reads an issue type, filters on a status, or performs
a transition. Nothing about this instance's workflow can be asserted from the
evidence available.
