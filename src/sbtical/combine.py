"""Per-person feeds that combine parts of several schools' feeds.

For a student who splits the day between schools (e.g. mornings at one,
afternoons at another), each part keeps following its own school's calendar.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .ical import _new_calendar, build_feed, build_student_feed
from .model import CalendarError, School, load_school
from .resolve import resolve_days


@dataclass(frozen=True)
class Source:
    school: School
    feed: str | None = None
    student: str | None = None


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    description: str | None
    sources: tuple[Source, ...]


def is_person_file(path: str | Path) -> bool:
    with Path(path).open() as f:
        return "person" in (yaml.safe_load(f) or {})


def load_person(path: str | Path) -> Person:
    path = Path(path)
    with path.open() as f:
        raw = yaml.safe_load(f) or {}
    p = raw.get("person") or {}
    if "id" not in p or "name" not in p:
        raise CalendarError(f"{path}: person.id and person.name are required")
    sources = []
    for i, s in enumerate(raw.get("sources") or []):
        where = f"{path}: sources[{i}]"
        school = load_school(path.parent / s["school"])
        if bool(s.get("feed")) == bool(s.get("student")):
            raise CalendarError(f"{where}: give exactly one of 'feed' or 'student'")
        if s.get("feed") and s["feed"] not in {f.id for f in school.feeds}:
            raise CalendarError(f"{where}: {school.id} has no feed {s['feed']!r}")
        if s.get("student") and s["student"] not in {x.id for x in school.students}:
            raise CalendarError(f"{where}: {school.id} has no student {s['student']!r}")
        sources.append(Source(school, s.get("feed"), s.get("student")))
    if not sources:
        raise CalendarError(f"{path}: needs at least one source")
    return Person(str(p["id"]), p["name"], p.get("description"), tuple(sources))


def build_person_feed(person: Person):
    first = person.sources[0].school
    cal = _new_calendar(first, person.name, person.description)
    for src in person.sources:
        days = resolve_days(src.school)
        if src.feed:
            feed = next(f for f in src.school.feeds if f.id == src.feed)
            part = build_feed(src.school, feed, days=days)
        else:
            student = next(x for x in src.school.students if x.id == src.student)
            part = build_student_feed(src.school, student, days=days)
        for ev in part.walk("VEVENT"):
            cal.add_component(ev)
    cal.add_missing_timezones()
    return cal
