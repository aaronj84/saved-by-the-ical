"""Turn resolved school days into iCalendar feeds."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable

from icalendar import Calendar, Event as VEvent, vDuration

from .model import Feed, School, Student
from .resolve import Day, resolve_days

PRODID = "-//saved-by-the-ical//sbtical//EN"
UID_DOMAIN = "saved-by-the-ical"
REFRESH = dt.timedelta(hours=6)


def _new_calendar(school: School, name: str, description: str | None) -> Calendar:
    cal = Calendar()
    cal.add("prodid", PRODID)
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-timezone", school.timezone.key)
    if description:
        cal.add("x-wr-caldesc", description)
    # Hints for how often subscribed clients should re-fetch.
    cal.add("refresh-interval", REFRESH, parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", vDuration(REFRESH))
    return cal


def _event(uid: str, summary: str, stamp: dt.datetime, **props) -> VEvent:
    ev = VEvent()
    ev.add("uid", f"{uid}@{UID_DOMAIN}")
    ev.add("dtstamp", stamp)
    ev.add("summary", summary)
    for key, value in props.items():
        if value is None or value == () or value == []:
            continue
        ev.add(key.replace("_", "-"), value)
    return ev


def _title(school: School, text: str) -> str:
    return f"{school.short_name} {text}" if school.short_name else text


def default_stamp(school: School) -> dt.datetime:
    """Fixed DTSTAMP so rebuilding unchanged data produces identical files
    (clean git diffs). Subscribed calendars re-read the whole feed anyway."""
    return dt.datetime.combine(school.first_day, dt.time(), tzinfo=dt.timezone.utc)


def _at(school: School, d: dt.date, t: dt.time) -> dt.datetime:
    return dt.datetime.combine(d, t, tzinfo=school.timezone)


# --------------------------------------------------------------------------
# Components. Each yields VEVENTs; UIDs are deterministic so that rebuilding
# a feed updates events in subscribers' calendars instead of duplicating them.


def bell_events(
    school: School,
    days: Iterable[Day],
    stamp: dt.datetime,
    scope: str,
    include_breaks: bool = False,
    periods: tuple[str, ...] = (),
    group: str | None = None,
):
    for day in days:
        if not day.in_session or not day.schedule:
            continue
        for p in day.schedule.periods:
            if p.kind != "class" and not include_breaks:
                continue
            if periods and p.id not in periods:
                continue
            if group and p.group and p.group != group:
                continue
            desc = day.schedule.name + (f" · {day.rotation} Day" if day.rotation else "")
            yield _event(
                f"{school.id}-{scope}-{day.date.isoformat()}-p{p.id}",
                _title(school, p.name_for(day.rotation)),
                stamp,
                dtstart=_at(school, day.date, p.start),
                dtend=_at(school, day.date, p.end),
                description=desc,
                transp="OPAQUE" if p.kind == "class" else "TRANSPARENT",
            )


def _terms_on(school: School, d: dt.date) -> list[str]:
    return [name for name, (start, end) in school.terms.items() if start <= d <= end]


def student_bell_events(school: School, student: Student, days: Iterable[Day], stamp):
    for day in days:
        if not day.in_session or not day.schedule:
            continue
        terms = _terms_on(school, day.date)
        for p in day.schedule.periods:
            if p.group and p.group != student.group:
                continue
            options = student.classes.get(p.class_slot)
            assignment = None
            if options:
                # Most specific first: term, then rotation label, then every day.
                keys = [*terms, day.rotation or "", "*"]
                assignment = next((options[k] for k in keys if k in options), None)
            if assignment is None:
                if p.kind == "other" or (p.kind != "class" and student.include_breaks):
                    summary, location, details = p.name_for(day.rotation), None, []
                else:
                    continue
            else:
                summary, location = assignment.title, assignment.location
                if p.student_title:
                    summary = p.student_title.format(**{"class": assignment.title})
                details = [p.name_for(day.rotation)]
                if assignment.teacher:
                    details.append(assignment.teacher)
            details.append(day.schedule.name + (f" · {day.rotation} Day" if day.rotation else ""))
            yield _event(
                f"{school.id}-{student.id}-{day.date.isoformat()}-p{p.id}",
                summary,
                stamp,
                dtstart=_at(school, day.date, p.start),
                dtend=_at(school, day.date, p.end),
                location=location,
                description="\n".join(details),
            )


def day_label_events(school: School, days: Iterable[Day], stamp, scope: str):
    """One all-day event per school day: 'A Day', 'B Day · Early Release', ..."""
    for day in days:
        if not day.in_session:
            continue
        parts = []
        if day.rotation:
            parts.append(f"{day.rotation} Day")
        default = school.weekly.get(day.date.weekday())
        if day.schedule and day.schedule.id != default and not (
            school.rotation and day.schedule.id in school.rotation.schedules.values()
        ):
            parts.append(day.schedule.name)
        if not parts:
            continue
        yield _event(
            f"{school.id}-{scope}-{day.date.isoformat()}-label",
            _title(school, " · ".join(parts)),
            stamp,
            dtstart=day.date,
            dtend=day.date + dt.timedelta(days=1),
            transp="TRANSPARENT",
        )


def _visible_overrides(school: School, days: Iterable[Day]):
    """Titled overrides that still matter after later overrides are applied.

    An override is shown if, on at least one of its dates, it is the last
    titled override for that date and (for no-school entries) that date really
    is off. So a school file can cancel or retitle a district day off without
    the district's title leaking into its calendar.
    """
    in_session = {d.date: d.in_session for d in days}
    winner: dict[dt.date, int] = {}
    for i, o in enumerate(school.overrides):
        if not o.title:
            continue
        d = o.start
        while d <= o.end:
            winner[d] = i
            d += dt.timedelta(days=1)
    for i, o in enumerate(school.overrides):
        if not o.title:
            continue
        d = o.start
        while d <= o.end:
            if winner.get(d) == i and not (o.no_school and in_session.get(d, False)):
                yield o
                break
            d += dt.timedelta(days=1)


def override_events(school: School, days: Iterable[Day], stamp, scope: str, no_school_only=False):
    """Titled day overrides (holidays, breaks, early outs) as all-day events."""
    for o in _visible_overrides(school, days):
        if no_school_only and not o.no_school:
            continue
        yield _event(
            f"{school.id}-{scope}-day-{o.start.isoformat()}-{o.end.isoformat()}",
            o.title,
            stamp,
            dtstart=o.start,
            dtend=o.end + dt.timedelta(days=1),  # DTEND is exclusive for dates
            description=o.description,
            categories=list(o.categories) or None,
            transp="TRANSPARENT",
        )


def school_events(school: School, stamp, scope: str, categories: tuple[str, ...] = ()):
    for e in school.events:
        if categories and not set(categories) & set(e.categories):
            continue
        if e.all_day:
            start, end = e.start_date, e.end_date + dt.timedelta(days=1)
        else:
            start = _at(school, e.start_date, e.start_time)
            end = _at(school, e.end_date, e.end_time)
        yield _event(
            f"{school.id}-{scope}-event-{e.id}",
            e.title,
            stamp,
            dtstart=start,
            dtend=end,
            location=e.location,
            description=e.description,
            categories=list(e.categories) or None,
            transp="TRANSPARENT" if e.all_day else "OPAQUE",
        )


# --------------------------------------------------------------------------
# Feed builders


def build_feed(school: School, feed: Feed, stamp: dt.datetime | None = None, days=None) -> Calendar:
    stamp = stamp or default_stamp(school)
    all_days = days if days is not None else resolve_days(school)
    days = [d for d in all_days if feed.includes(d)]
    cal = _new_calendar(school, f"{school.name} · {feed.name}", feed.description)
    scope = feed.id
    for part in feed.include:
        if part == "bells":
            events = bell_events(
                school, days, stamp, scope, feed.include_breaks, feed.periods, feed.group
            )
        elif part == "day_labels":
            events = day_label_events(school, days, stamp, scope)
        elif part == "days":
            events = override_events(school, all_days, stamp, scope, feed.no_school_only)
        elif part == "events":
            events = school_events(school, stamp, scope, feed.categories)
        for ev in events:
            cal.add_component(ev)
    cal.add_missing_timezones()
    return cal


def build_student_feed(school: School, student: Student, stamp=None, days=None) -> Calendar:
    stamp = stamp or default_stamp(school)
    days = days if days is not None else resolve_days(school)
    cal = _new_calendar(school, student.name, student.description)
    for part in student.include:
        if part == "bells":
            events = student_bell_events(school, student, days, stamp)
        elif part == "day_labels":
            events = day_label_events(school, days, stamp, student.id)
        elif part == "days":
            events = override_events(school, days, stamp, student.id)
        elif part == "events":
            events = school_events(school, stamp, student.id)
        else:
            continue
        for ev in events:
            cal.add_component(ev)
    cal.add_missing_timezones()
    return cal
