"""Resolve each calendar date into a concrete day: school or not, which bell
schedule, and which rotation label (A/B day, etc.)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .model import BellSchedule, DayOverride, School


@dataclass(frozen=True)
class Day:
    date: dt.date
    in_session: bool
    schedule: BellSchedule | None
    rotation: str | None
    overrides: tuple[DayOverride, ...]  # every override touching this date

    @property
    def titles(self) -> list[str]:
        return [o.title for o in self.overrides if o.title]


def resolve_days(school: School) -> list[Day]:
    """Walk the school year day by day.

    Precedence (later wins): weekly pattern -> rotation schedule -> overrides
    in file order. Listing a single-day override after a range lets you carve
    an exception out of it.
    """
    days: list[Day] = []
    rot = school.rotation
    rot_index = 0
    d = school.first_day
    while d <= school.last_day:
        matching = tuple(o for o in school.overrides if o.start <= d <= o.end)

        schedule_id = school.weekly.get(d.weekday())
        in_session = schedule_id is not None
        forced_label = None
        advance = True
        for o in matching:
            if o.no_school:
                in_session, schedule_id = False, None
            if o.schedule:
                in_session, schedule_id = True, o.schedule
            if o.rotation is not None:
                forced_label = o.rotation
            if not o.advance_rotation:
                advance = False

        label = None
        if in_session and rot and d >= rot.start:
            if forced_label is not None:
                label = forced_label
                # Re-sync so the cycle continues from the forced label.
                rot_index = rot.labels.index(forced_label)
            else:
                label = rot.labels[rot_index % len(rot.labels)]
            # A rotation-specific schedule applies unless an override set one.
            if label in rot.schedules and not any(o.schedule for o in matching):
                schedule_id = rot.schedules[label]
            if advance:
                rot_index += 1

        days.append(
            Day(
                date=d,
                in_session=in_session,
                schedule=school.bell_schedules[schedule_id] if schedule_id else None,
                rotation=label,
                overrides=matching,
            )
        )
        d += dt.timedelta(days=1)
    return days
