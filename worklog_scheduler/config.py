"""Schedule configuration and its on-disk form.

The file stays at ``~/.jira_worklog_manager.json`` with the same key names it has
always had, so a config written by the original Tkinter tool loads unchanged.
Unknown keys are dropped on load and a corrupt file falls back to the defaults —
both behaviours inherited deliberately (``work_log.py:140-163``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

from trackspace.kb import KnowledgeBase, load_kb

from .clock import today_local

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

#: Unchanged from the original tool so existing configs keep working.
CONFIG_PATH = Path.home() / ".jira_worklog_manager.json"


@dataclass
class RecurringMeeting:
    """A meeting that repeats on given weekdays, every week or every N weeks."""

    weekdays: list[int]
    hour: int
    minute: int
    duration_min: int
    comment: str
    #: 1 = weekly, 2 = every other week, and so on.
    interval_weeks: int = 1
    #: A date in a week the meeting *does* happen. Empty means "count from the
    #: start of the range", which is what a fresh bi-weekly meeting gets.
    anchor: str = ""
    #: SC-7: dates this one meeting is cancelled on, without touching any other
    #: meeting that day. Config-wide ``exclude_dates`` still suppresses everything.
    skip_dates: list[str] = field(default_factory=list)

    def weekdays_str(self) -> str:
        selected = set(self.weekdays)
        if selected == {0, 1, 2, 3, 4}:
            return "Mon-Fri"
        if selected == {0, 1, 2, 3, 4, 5, 6}:
            return "Daily"
        return ",".join(WEEKDAY_NAMES[i] for i in sorted(selected))

    def time_str(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"

    def repeat_str(self) -> str:
        if self.interval_weeks <= 1:
            return "weekly"
        every = (
            "every other week" if self.interval_weeks == 2 else f"every {self.interval_weeks} weeks"
        )
        return f"{every} from {self.anchor}" if self.anchor else every

    def occurs_on(self, day: date, anchor_week: date) -> bool:
        """Does this meeting happen on ``day``?

        ``anchor_week`` is the Monday the counting starts from — the meeting's own
        anchor when it has one, otherwise the caller's fallback.
        """
        if day.weekday() not in self.weekdays:
            return False
        if day.isoformat() in self.skip_dates:
            return False
        if self.interval_weeks <= 1:
            return True
        weeks_apart = ((day - timedelta(days=day.weekday())) - anchor_week).days // 7
        return weeks_apart % self.interval_weeks == 0


@dataclass
class OneOffMeeting:
    """A meeting on one specific date."""

    date: str  # YYYY-MM-DD
    hour: int
    minute: int
    duration_min: int
    comment: str

    def time_str(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


@dataclass
class ScheduleConfig:
    """Everything the tool needs to compute and post a set of worklogs."""

    issue_key: str
    trackspace_base: str
    timezone: str
    start_date: str
    end_date: str
    exclude_dates: list[str] = field(default_factory=list)
    recurring: list[RecurringMeeting] = field(default_factory=list)
    oneoffs: list[OneOffMeeting] = field(default_factory=list)
    dry_run: bool = True

    # ---- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """The historical JSON shape, including the ``jira_base`` key name."""
        return {
            "issue_key": self.issue_key,
            "jira_base": self.trackspace_base,
            "timezone": self.timezone,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "exclude_dates": list(self.exclude_dates),
            "recurring": [asdict(r) for r in self.recurring],
            "oneoffs": [asdict(o) for o in self.oneoffs],
            "dry_run": self.dry_run,
        }

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any], kb: KnowledgeBase | None = None) -> ScheduleConfig:
        base = cls.defaults(kb)
        return cls(
            issue_key=str(data.get("issue_key", base.issue_key)),
            trackspace_base=str(data.get("jira_base", base.trackspace_base)),
            timezone=str(data.get("timezone", base.timezone)),
            start_date=str(data.get("start_date", base.start_date)),
            end_date=str(data.get("end_date", base.end_date)),
            exclude_dates=[str(d) for d in data.get("exclude_dates", [])],
            recurring=[
                RecurringMeeting(
                    weekdays=[int(w) for w in r["weekdays"]],
                    hour=int(r["hour"]),
                    minute=int(r["minute"]),
                    duration_min=int(r["duration_min"]),
                    comment=str(r["comment"]),
                    # Absent in configs written before bi-weekly existed.
                    interval_weeks=int(r.get("interval_weeks", 1) or 1),
                    anchor=str(r.get("anchor", "")),
                    # SC-7: absent in configs written before per-meeting blackout
                    # dates existed.
                    skip_dates=[str(d) for d in r.get("skip_dates", [])],
                )
                for r in data.get("recurring", [])
            ],
            oneoffs=[
                OneOffMeeting(
                    date=str(o["date"]),
                    hour=int(o["hour"]),
                    minute=int(o["minute"]),
                    duration_min=int(o["duration_min"]),
                    comment=str(o["comment"]),
                )
                for o in data.get("oneoffs", [])
            ],
            dry_run=bool(data.get("dry_run", base.dry_run)),
        )

    @classmethod
    def defaults(cls, kb: KnowledgeBase | None = None, today: date | None = None) -> ScheduleConfig:
        """The out-of-the-box schedule, taken from the knowledge base."""
        knowledge = kb or load_kb()
        now = today or today_local()
        start = now.replace(day=1)
        end = start + timedelta(days=27)
        recurring = [
            RecurringMeeting(
                weekdays=[int(w) for w in entry["weekdays"]],
                hour=int(entry["hour"]),
                minute=int(entry["minute"]),
                duration_min=int(entry["duration_min"]),
                interval_weeks=int(entry.get("interval_weeks", 1) or 1),
                anchor=str(entry.get("anchor", "")),
                comment=str(entry["comment"]),
            )
            for entry in cast(list[dict[str, Any]], knowledge.default("recurring_meetings"))
        ]
        return cls(
            issue_key=str(knowledge.default("issue_key")),
            trackspace_base=knowledge.base_url,
            timezone=str(knowledge.default("timezone")),
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            exclude_dates=[],
            recurring=recurring,
            oneoffs=[],
            dry_run=bool(knowledge.default("dry_run")),
        )

    @classmethod
    def load(
        cls,
        path: Path = CONFIG_PATH,
        kb: KnowledgeBase | None = None,
        today: date | None = None,
    ) -> ScheduleConfig:
        """Load the saved config, falling back to defaults on anything unreadable."""
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return cls.from_dict(data, kb)
            except (OSError, ValueError, KeyError, TypeError):
                # A config that cannot be read is replaced by the defaults rather
                # than blocking the tool — same as the original.
                pass
        return cls.defaults(kb, today)

    # ---- convenience -------------------------------------------------------
    def sort_oneoffs(self) -> None:
        self.oneoffs.sort(key=lambda o: (o.date, o.hour, o.minute))

    def add_exclusion(self, day: date) -> bool:
        """Add an excluded date. Returns ``False`` if it was already there."""
        iso = day.isoformat()
        if iso in self.exclude_dates:
            return False
        self.exclude_dates.append(iso)
        self.exclude_dates.sort()
        return True
