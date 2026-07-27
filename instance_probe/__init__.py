"""NT-1: a read-only probe that turns declared-unknown Trackspace facts into
sourced findings.

See ``kb/proposals/capability-audit.md`` (entries NT-1 and KBW-1 through
KBW-6) for the design brief and ``kb/build-notes.md`` for the build's design
decisions. This package never writes to the knowledge base: it only ever
performs GET requests (enforced by :func:`instance_probe.probe.assert_get_only`)
and prints/exports a findings report for a human to fold into
``kb/trackspace.json``.
"""

from __future__ import annotations
