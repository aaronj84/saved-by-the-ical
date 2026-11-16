"""Carpool runs tied to school calendars, emitted as feeds.

A run has one or more stops (school + pickup time). Each stop follows its
school's calendar, so holidays drop out automatically; a run happens when at
least one of its stops is in session on a matching weekday / A/B day.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import yaml

from .ical import _at, _event, _new_calendar, default_stamp
from .model import WEEKDAYS, CalendarError, School, _time, _tuple, load_school
from .resolve import resolve_days


@dataclass(frozen=True)
class Stop:
    school: School
    time: dt.time
    kids: tuple[str, ...]


@dataclass(frozen=True)
class Rule:
    title: str
    short: str
    stops: tuple[Stop, ...]
    before: int  # minutes before the first stop
    after: int   # minutes after the last stop
    days: frozenset[int]  # weekday indexes; empty = every school day
    rotation: tuple[str, ...]
    driver: str | None


@dataclass(frozen=True)
class Occurrence:
    date: dt.date
    start: dt.datetime
    end: dt.datetime
    rule: Rule
    stops: tuple[tuple[dt.datetime, Stop], ...]  # stops in session that day

    @property
    def kids(self) -> tuple[str, ...]:
        return tuple(k for _, s in self.stops for k in s.kids)

    @property
    def summary(self) -> str:
        groups = [" & ".join(s.kids) for _, s in self.stops if s.kids]
        return f"{self.rule.title} ({', '.join(groups)})" if groups else self.rule.title


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
        stops = []
        for j, st in enumerate(r.get("stops") or []):
            spath = (path.parent / st["school"]).resolve()
            if spath not in schools:
                schools[spath] = load_school(spath)
            school = schools[spath]
            stops.append(Stop(school, _time(st["time"], f"{where}.stops[{j}]"),
                              _tuple(st.get("kids")) or _school_kids(school)))
        if not stops:
            raise CalendarError(f"{where}: needs at least one stop")
        stops.sort(key=lambda s: s.time)
        days = set()
        for d in _tuple(r.get("days")):
            if d not in WEEKDAYS:
                raise CalendarError(f"{where}: unknown weekday {d!r} (use mon..sun)")
            days.add(WEEKDAYS.index(d))
        rotation = _tuple(r.get("rotation"))
        rot_school = stops[0].school
        if rotation and not rot_school.rotation:
            raise CalendarError(f"{where}: {rot_school.id} has no A/B rotation")
        if rotation and set(rotation) - set(rot_school.rotation.labels):
            raise CalendarError(f"{where}: unknown rotation labels {rotation}")
        rules.append(Rule(
            title=r["title"], short=r.get("short", r["title"]), stops=tuple(stops),
            before=int(r.get("before", 0)), after=int(r.get("after", 10)),
            days=frozenset(days), rotation=rotation, driver=r.get("driver"),
        ))
    drivers = {r.driver for r in rules if r.driver}
    feeds = []
    for f in raw.get("feeds") or []:
        if f.get("driver") and f["driver"] not in drivers:
            raise CalendarError(f"{path}: feed {f['id']!r}: no rules for driver {f['driver']!r}")
        feeds.append(CarpoolFeed(str(f["id"]), f.get("name", f["id"]), f.get("driver"), f.get("description")))
    return Carpool(tuple(rules), tuple(feeds))


def occurrences(carpool: Carpool) -> list[Occurrence]:
    days_by_school: dict[str, dict] = {}

    def days_of(school: School):
        if school.id not in days_by_school:
            days_by_school[school.id] = {d.date: d for d in resolve_days(school)}
        return days_by_school[school.id]

    out = []
    for rule in carpool.rules:
        dates = sorted({d for st in rule.stops for d in days_of(st.school)})
        for date in dates:
            if rule.days and date.weekday() not in rule.days:
                continue
            if rule.rotation:
                first = days_of(rule.stops[0].school).get(date)
                if not first or first.rotation not in rule.rotation:
                    continue
            live = []
            for st in rule.stops:
                day = days_of(st.school).get(date)
                if day and day.in_session:
                    live.append((_at(st.school, date, st.time), st))
            if not live:
                continue
            start = live[0][0] - dt.timedelta(minutes=rule.before)
            end = live[-1][0] + dt.timedelta(minutes=rule.after)
            out.append(Occurrence(date, start, end, rule, tuple(live)))
    out.sort(key=lambda o: (o.start, o.rule.title))
    return out


def build_carpool_feed(carpool: Carpool, feed: CarpoolFeed):
    first = carpool.rules[0].stops[0].school
    cal = _new_calendar(first, feed.name, feed.description)
    stamp = default_stamp(first)
    for o in occurrences(carpool):
        if feed.driver and o.rule.driver != feed.driver:
            continue
        slug = "".join(c for c in o.rule.title.lower() if c.isalnum())
        stops = [f"{t:%-I:%M} {s.school.name}" + (f" ({', '.join(s.kids)})" if s.kids else "")
                 for t, s in o.stops]
        cal.add_component(_event(
            f"carpool-{o.date.isoformat()}-{o.start:%H%M}-{slug}",
            o.summary, stamp, dtstart=o.start, dtend=o.end,
            location=" → ".join(s.school.name for _, s in o.stops),
            description="\n".join(stops) + (f"\nDriver: {o.rule.driver}" if o.rule.driver else ""),
        ))
    cal.add_missing_timezones()
    return cal
