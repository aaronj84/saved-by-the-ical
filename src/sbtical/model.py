"""Data model for a school calendar, loaded from a YAML file.

The YAML is the single source of truth. Documents (bell schedules, district
calendars, event lists) are parsed into this shape; every iCal feed is a
filtered view over it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class CalendarError(ValueError):
    """Raised when calendar data is invalid or inconsistent."""


@dataclass(frozen=True)
class Period:
    id: str
    name: str
    start: dt.time
    end: dt.time
    kind: str = "class"  # class | break | passing | other


@dataclass(frozen=True)
class BellSchedule:
    id: str
    name: str
    periods: tuple[Period, ...]


@dataclass(frozen=True)
class DayOverride:
    start: dt.date
    end: dt.date  # inclusive
    title: str | None = None
    no_school: bool = False
    schedule: str | None = None
    rotation: str | None = None  # force a rotation label for these days
    advance_rotation: bool = True  # if False, school day doesn't advance A/B
    categories: tuple[str, ...] = ()
    description: str | None = None


@dataclass(frozen=True)
class Event:
    id: str
    title: str
    start_date: dt.date
    end_date: dt.date  # inclusive
    start_time: dt.time | None = None
    end_time: dt.time | None = None
    location: str | None = None
    description: str | None = None
    categories: tuple[str, ...] = ()

    @property
    def all_day(self) -> bool:
        return self.start_time is None


@dataclass(frozen=True)
class Rotation:
    labels: tuple[str, ...]
    start: dt.date  # first school day on or after this gets labels[0]
    schedules: dict[str, str] = field(default_factory=dict)  # label -> schedule


@dataclass(frozen=True)
class Feed:
    id: str
    name: str
    include: tuple[str, ...]  # bells | days | day_labels | events
    categories: tuple[str, ...] = ()  # filter events/overrides by category
    no_school_only: bool = False
    include_breaks: bool = False
    periods: tuple[str, ...] = ()  # restrict bells to these period ids
    description: str | None = None


@dataclass(frozen=True)
class ClassAssignment:
    title: str
    location: str | None = None
    teacher: str | None = None


@dataclass(frozen=True)
class Student:
    """A per-person feed: bell periods relabeled with that person's classes."""

    id: str
    name: str
    # period id -> rotation label (or "*" for every day) -> assignment
    classes: dict[str, dict[str, ClassAssignment]]
    include_breaks: bool = False
    include: tuple[str, ...] = ("bells", "days")


@dataclass(frozen=True)
class School:
    id: str
    name: str
    timezone: ZoneInfo
    first_day: dt.date
    last_day: dt.date
    bell_schedules: dict[str, BellSchedule]
    weekly: dict[int, str | None]  # weekday index -> schedule id (None = no school)
    rotation: Rotation | None
    overrides: tuple[DayOverride, ...]
    events: tuple[Event, ...]
    feeds: tuple[Feed, ...]
    students: tuple[Student, ...]


# --------------------------------------------------------------------------
# Parsing helpers


def _date(value, where: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as e:
        raise CalendarError(f"{where}: invalid date {value!r}") from e


def _time(value, where: str) -> dt.time:
    # YAML 1.1 parses unquoted 08:30 as sexagesimal int (510); accept that too.
    if isinstance(value, int):
        return dt.time(value // 60, value % 60)
    try:
        return dt.time.fromisoformat(str(value).strip())
    except ValueError as e:
        raise CalendarError(f"{where}: invalid time {value!r} (use HH:MM)") from e


def _tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def _date_range(raw: dict, where: str) -> tuple[dt.date, dt.date]:
    if "date" in raw:
        d = _date(raw["date"], where)
        return d, d
    if "start" in raw and "end" in raw:
        s, e = _date(raw["start"], where), _date(raw["end"], where)
        if e < s:
            raise CalendarError(f"{where}: end {e} is before start {s}")
        return s, e
    raise CalendarError(f"{where}: needs 'date' or 'start'+'end'")


# --------------------------------------------------------------------------
# Loader


def load_school(path: str | Path) -> School:
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f)
    try:
        return parse_school(raw)
    except CalendarError as e:
        raise CalendarError(f"{path}: {e}") from e


def parse_school(raw: dict) -> School:
    s = raw.get("school") or {}
    for key in ("id", "name", "timezone"):
        if key not in s:
            raise CalendarError(f"school.{key} is required")
    try:
        tz = ZoneInfo(s["timezone"])
    except Exception as e:
        raise CalendarError(f"school.timezone: unknown zone {s['timezone']!r}") from e

    year = raw.get("year") or {}
    first_day = _date(year.get("first_day"), "year.first_day")
    last_day = _date(year.get("last_day"), "year.last_day")
    if last_day < first_day:
        raise CalendarError("year.last_day is before year.first_day")

    schedules: dict[str, BellSchedule] = {}
    for sid, sraw in (raw.get("bell_schedules") or {}).items():
        periods = []
        for i, p in enumerate(sraw.get("periods") or []):
            where = f"bell_schedules.{sid}.periods[{i}]"
            start, end = _time(p["start"], where), _time(p["end"], where)
            if end <= start:
                raise CalendarError(f"{where}: end {end} is not after start {start}")
            periods.append(
                Period(
                    id=str(p.get("id", p["name"])),
                    name=str(p["name"]),
                    start=start,
                    end=end,
                    kind=p.get("kind", "class"),
                )
            )
        periods.sort(key=lambda p: p.start)
        for a, b in zip(periods, periods[1:]):
            if b.start < a.end:
                raise CalendarError(
                    f"bell_schedules.{sid}: '{a.name}' ({a.start}-{a.end}) overlaps "
                    f"'{b.name}' ({b.start}-{b.end})"
                )
        ids = [p.id for p in periods]
        if len(ids) != len(set(ids)):
            raise CalendarError(f"bell_schedules.{sid}: duplicate period ids {ids}")
        schedules[sid] = BellSchedule(sid, sraw.get("name", sid), tuple(periods))

    def check_schedule(ref, where):
        if ref is not None and ref not in schedules:
            raise CalendarError(f"{where}: unknown bell schedule {ref!r}")

    weekly_raw = raw.get("weekly") or {}
    weekly: dict[int, str | None] = {}
    for i, name in enumerate(WEEKDAYS):
        ref = weekly_raw.get(name)
        check_schedule(ref, f"weekly.{name}")
        weekly[i] = ref

    rotation = None
    if raw.get("rotation"):
        r = raw["rotation"]
        labels = _tuple(r.get("labels"))
        if not labels:
            raise CalendarError("rotation.labels must be non-empty")
        rsched = {str(k): v for k, v in (r.get("schedules") or {}).items()}
        for label, ref in rsched.items():
            if label not in labels:
                raise CalendarError(f"rotation.schedules: unknown label {label!r}")
            check_schedule(ref, f"rotation.schedules.{label}")
        rotation = Rotation(
            labels=labels,
            start=_date(r.get("start", first_day), "rotation.start"),
            schedules=rsched,
        )

    overrides = []
    for i, o in enumerate(raw.get("days") or []):
        where = f"days[{i}]"
        start, end = _date_range(o, where)
        check_schedule(o.get("schedule"), where)
        if o.get("no_school") and o.get("schedule"):
            raise CalendarError(f"{where}: can't set both no_school and schedule")
        if o.get("rotation") is not None and rotation and str(o["rotation"]) not in rotation.labels:
            raise CalendarError(f"{where}: unknown rotation label {o['rotation']!r}")
        overrides.append(
            DayOverride(
                start=start,
                end=end,
                title=o.get("title"),
                no_school=bool(o.get("no_school", False)),
                schedule=o.get("schedule"),
                rotation=str(o["rotation"]) if o.get("rotation") is not None else None,
                advance_rotation=bool(o.get("advance_rotation", True)),
                categories=_tuple(o.get("categories")),
                description=o.get("description"),
            )
        )

    events = []
    seen_ids: set[str] = set()
    for i, e in enumerate(raw.get("events") or []):
        where = f"events[{i}]"
        start, end = _date_range(e, where)
        st = _time(e["start_time"], where) if e.get("start_time") is not None else None
        et = _time(e["end_time"], where) if e.get("end_time") is not None else None
        if (st is None) != (et is None):
            raise CalendarError(f"{where}: give both start_time and end_time, or neither")
        if st and start == end and et <= st:
            raise CalendarError(f"{where}: end_time is not after start_time")
        eid = str(e.get("id") or f"{start.isoformat()}-{_slug(e['title'])}")
        if eid in seen_ids:
            raise CalendarError(f"{where}: duplicate event id {eid!r}; set an explicit id")
        seen_ids.add(eid)
        events.append(
            Event(
                id=eid,
                title=e["title"],
                start_date=start,
                end_date=end,
                start_time=st,
                end_time=et,
                location=e.get("location"),
                description=e.get("description"),
                categories=_tuple(e.get("categories")),
            )
        )

    feeds = []
    for f in raw.get("feeds") or []:
        include = _tuple(f.get("include"))
        bad = set(include) - {"bells", "days", "day_labels", "events"}
        if bad:
            raise CalendarError(f"feeds.{f.get('id')}: unknown include {sorted(bad)}")
        feeds.append(
            Feed(
                id=str(f["id"]),
                name=f.get("name", f["id"]),
                include=include,
                categories=_tuple(f.get("categories")),
                no_school_only=bool(f.get("no_school_only", False)),
                include_breaks=bool(f.get("include_breaks", False)),
                periods=_tuple(f.get("periods")),
                description=f.get("description"),
            )
        )

    students = []
    for st_raw in raw.get("students") or []:
        classes: dict[str, dict[str, ClassAssignment]] = {}
        for pid, val in (st_raw.get("classes") or {}).items():
            pid = str(pid)
            # Either a single assignment, or {A: {...}, B: {...}} by rotation label.
            if isinstance(val, str):
                classes[pid] = {"*": ClassAssignment(val)}
            elif "title" in val:
                classes[pid] = {"*": _assignment(val)}
            else:
                classes[pid] = {
                    str(label): _assignment(a) if isinstance(a, dict) else ClassAssignment(str(a))
                    for label, a in val.items()
                }
        students.append(
            Student(
                id=str(st_raw["id"]),
                name=st_raw.get("name", st_raw["id"]),
                classes=classes,
                include_breaks=bool(st_raw.get("include_breaks", False)),
                include=_tuple(st_raw.get("include")) or ("bells", "days"),
            )
        )

    all_ids = [f.id for f in feeds] + [s.id for s in students]
    dupes = {i for i in all_ids if all_ids.count(i) > 1}
    if dupes:
        raise CalendarError(f"duplicate feed/student ids: {sorted(dupes)}")

    return School(
        id=str(s["id"]),
        name=s["name"],
        timezone=tz,
        first_day=first_day,
        last_day=last_day,
        bell_schedules=schedules,
        weekly=weekly,
        rotation=rotation,
        overrides=tuple(overrides),
        events=tuple(events),
        feeds=tuple(feeds),
        students=tuple(students),
    )


def _assignment(raw: dict) -> ClassAssignment:
    return ClassAssignment(
        title=str(raw["title"]), location=raw.get("location"), teacher=raw.get("teacher")
    )


def _slug(text: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in text)
    return "-".join(filter(None, out.split("-")))
