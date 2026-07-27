"""``python -m worklog_scheduler``."""

from __future__ import annotations

import sys

from .schedule_and_post_worklogs import main

if __name__ == "__main__":
    sys.exit(main())
