"""Carpool pickups: rules tied to a school's calendar, emitted as feeds.

A rule produces one short event on every in-session day of its school that
matches its weekdays and rotation labels, so holidays and breaks drop out
automatically.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import yaml

from .ical import _new_calendar, _at, _event, default_stamp
from .model import WEEKDAYS, CalendarError, School, _time, _tuple, load_school
from .resolve import resolve_days


@dataclass(frozen=True)
class Rule:
    title: str
    short: str
    school: School
    kids: tuple[str, ...]
    time: dt.time
    minutes: int
    days: frozenset[int]  # weekday indexes; empty = every school day
    rotation: tuple[str, ...]
    driver: str | None

    @property
    def summary(self) -> str:
        return f"{self.title} ({' & '.join(self.kids)})"


@dataclass(frozen=True)
class CarpoolFeed:
    id: str
    name: str
    driver: str | None
    description: str | None


@dataclass(frozen=True)
class Carpool:
    rules: tuple[Rule, ...]
    feeds: tuple[CarpoolFeed, ...]


def is_carpool_file(path: str | Path) -> bool:
    with Path(path).open() as f:
        return "carpool" in (yaml.safe_load(f) or {})


def _school_kids(school: School) -> tuple[str, ...]:
    """Initials of the kids with a published calendar at this school
    ("D School Calendar" -> D)."""
    out = []
    for st in school.students:
        first = st.name.split()[0]
        if st.publish and len(first) == 1:
            out.append(first)
    return tuple(out)


def load_carpool(path: str | Path) -> Carpool:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    schools: dict[Path, School] = {}
    rules = []
    for i, r in enumerate(raw.get("carpool") or []):
        where = f"{path}: carpool[{i}]"
        spath = (path.parent / r["school"]).resolve()
        if spath not in schools:
            schools[spath] = load_school(spath)
        school = schools[spath]
        days = set()
        for d in _tuple(r.get("days")):
            if d not in WEEKDAYS:
                raise CalendarError(f"{where}: unknown weekday {d!r} (use mon..sun)")
            days.add(WEEKDAYS.index(d))
        rotation = _tuple(r.get("rotation"))
        if rotation and not school.rotation:
            raise CalendarError(f"{where}: {school.id} has no A/B rotation")
        if school.rotation and set(rotation) - set(school.rotation.labels):
            raise CalendarError(f"{where}: unknown rotation labels {rotation}")
        rules.append(Rule(
            title=r["title"], short=r.get("short", r["title"]), school=school,
            kids=_tuple(r.get("kids")) or _school_kids(school), time=_time(r["time"], where),
            minutes=int(r.get("minutes", 10)), days=frozenset(days),
            rotation=rotation, driver=r.get("driver"),
        ))
    drivers = {r.driver for r in rules if r.driver}
    feeds = []
    for f in raw.get("feeds") or []:
        if f.get("driver") and f["driver"] not in drivers:
            raise CalendarError(f"{path}: feed {f['id']!r}: no rules for driver {f['driver']!r}")
        feeds.append(CarpoolFeed(str(f["id"]), f.get("name", f["id"]), f.get("driver"), f.get("description")))
    return Carpool(tuple(rules), tuple(feeds))


def occurrences(carpool: Carpool):
    """Yield (date, start, end, rule) for every pickup, in date/time order."""
    days_by_school = {}
    out = []
    for rule in carpool.rules:
        sid = rule.school.id
        if sid not in days_by_school:
            days_by_school[sid] = resolve_days(rule.school)
        for day in days_by_school[sid]:
            if not day.in_session:
                continue
            if rule.days and day.date.weekday() not in rule.days:
                continue
            if rule.rotation and day.rotation not in rule.rotation:
                continue
            start = _at(rule.school, day.date, rule.time)
            out.append((day.date, start, start + dt.timedelta(minutes=rule.minutes), rule))
    out.sort(key=lambda x: (x[1], x[3].title))
    return out


def build_carpool_feed(carpool: Carpool, feed: CarpoolFeed):
    first = carpool.rules[0].school
    cal = _new_calendar(first, feed.name, feed.description)
    stamp = default_stamp(first)
    for date, start, end, rule in occurrences(carpool):
        if feed.driver and rule.driver != feed.driver:
            continue
        slug = "".join(c for c in rule.short.lower() if c.isalnum())
        cal.add_component(_event(
            f"carpool-{date.isoformat()}-{start:%H%M}-{rule.school.id}-{slug}",
            rule.summary, stamp, dtstart=start, dtend=end,
            location=rule.school.name,
            description=f"Kids: {', '.join(rule.kids)}" + (f"\nDriver: {rule.driver}" if rule.driver else ""),
        ))
    cal.add_missing_timezones()
    return cal
