import datetime as dt
from pathlib import Path

import pytest
from icalendar import Calendar

from sbtical.ical import build_feed, build_student_feed
from sbtical.model import CalendarError, load_school, parse_school
from sbtical.resolve import resolve_days

EXAMPLE = Path(__file__).parent.parent / "data/schools/example-high.yaml"
STAMP = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def base(**extra):
    raw = {
        "school": {"id": "t", "name": "T", "timezone": "America/New_York"},
        "year": {"first_day": "2026-09-07", "last_day": "2026-09-18"},  # Mon..Fri+1wk
        "bell_schedules": {
            "reg": {"periods": [
                {"id": "1", "name": "P1", "start": "08:00", "end": "09:00"},
                {"id": "L", "name": "Lunch", "start": "09:00", "end": "09:30", "kind": "break"},
                {"id": "2", "name": "P2", "start": "09:30", "end": "10:30"},
            ]},
            "short": {"periods": [{"id": "1", "name": "P1", "start": "08:00", "end": "08:30"}]},
        },
        "weekly": {d: "reg" for d in ["mon", "tue", "wed", "thu", "fri"]},
    }
    raw.update(extra)
    return parse_school(raw)


def by_date(school):
    return {d.date: d for d in resolve_days(school)}


def test_weekends_off_weekdays_on():
    days = by_date(base())
    assert days[dt.date(2026, 9, 7)].in_session
    assert not days[dt.date(2026, 9, 12)].in_session  # Saturday


def test_override_order_lets_single_day_carve_out_of_range():
    s = base(days=[
        {"start": "2026-09-07", "end": "2026-09-11", "no_school": True, "title": "Break"},
        {"date": "2026-09-09", "schedule": "short", "title": "Make-up day"},
    ])
    days = by_date(s)
    assert not days[dt.date(2026, 9, 8)].in_session
    assert days[dt.date(2026, 9, 9)].schedule.id == "short"


def test_rotation_skips_non_school_days():
    s = base(
        rotation={"labels": ["A", "B"]},
        days=[{"date": "2026-09-08", "no_school": True}],
    )
    days = by_date(s)
    labels = [days[dt.date(2026, 9, d)].rotation for d in (7, 8, 9, 10, 11, 14)]
    assert labels == ["A", None, "B", "A", "B", "A"]


def test_rotation_can_be_forced_and_held():
    s = base(
        rotation={"labels": ["A", "B"]},
        days=[
            {"date": "2026-09-08", "rotation": "A"},  # force A again
            {"date": "2026-09-09", "advance_rotation": False},  # assembly day: B, don't advance
        ],
    )
    days = by_date(s)
    labels = [days[dt.date(2026, 9, d)].rotation for d in (7, 8, 9, 10, 11)]
    assert labels == ["A", "A", "B", "B", "A"]


def test_rotation_specific_schedule():
    s = base(rotation={"labels": ["A", "B"], "schedules": {"B": "short"}})
    days = by_date(s)
    assert days[dt.date(2026, 9, 7)].schedule.id == "reg"
    assert days[dt.date(2026, 9, 8)].schedule.id == "short"


@pytest.mark.parametrize("bad, msg", [
    ({"weekly": {"mon": "nope"}}, "unknown bell schedule"),
    ({"days": [{"date": "2026-09-08", "no_school": True, "schedule": "reg"}]}, "both"),
    ({"bell_schedules": {"x": {"periods": [
        {"name": "a", "start": "08:00", "end": "09:00"},
        {"name": "b", "start": "08:30", "end": "09:30"}]}}}, "overlaps"),
    ({"events": [{"title": "x", "date": "2026-09-08", "start_time": "10:00"}]}, "both start_time"),
])
def test_validation(bad, msg):
    with pytest.raises(CalendarError, match=msg):
        base(**bad)


def test_unquoted_yaml_time_sexagesimal_is_accepted():
    s = base(bell_schedules={"reg": {"periods": [{"name": "P", "start": 480, "end": 540}]}})
    assert s.bell_schedules["reg"].periods[0].start == dt.time(8, 0)


def roundtrip(cal):
    return Calendar.from_ical(cal.to_ical())


def test_bell_feed_times_are_zoned_and_breaks_excluded():
    s = base(feeds=[{"id": "b", "include": ["bells"]}])
    cal = roundtrip(build_feed(s, s.feeds[0], STAMP))
    evs = cal.walk("VEVENT")
    assert len(evs) == 10 * 2  # 10 school days * 2 class periods
    first = evs[0]["DTSTART"].dt
    assert first.tzinfo is not None and str(first.tzinfo) == "America/New_York"
    assert {str(e["SUMMARY"]) for e in evs} == {"P1", "P2"}


def test_uids_are_stable_across_builds():
    s = base(feeds=[{"id": "b", "include": ["bells"]}])
    a = [str(e["UID"]) for e in build_feed(s, s.feeds[0], STAMP).walk("VEVENT")]
    b = [str(e["UID"]) for e in build_feed(s, s.feeds[0]).walk("VEVENT")]
    assert a == b and len(set(a)) == len(a)


def test_all_day_range_end_is_exclusive():
    s = base(
        days=[{"start": "2026-09-14", "end": "2026-09-18", "no_school": True, "title": "Break"}],
        feeds=[{"id": "d", "include": ["days"]}],
    )
    (ev,) = roundtrip(build_feed(s, s.feeds[0], STAMP)).walk("VEVENT")
    assert ev["DTSTART"].dt == dt.date(2026, 9, 14)
    assert ev["DTEND"].dt == dt.date(2026, 9, 19)


def test_student_feed_uses_rotation_specific_classes():
    s = base(
        rotation={"labels": ["A", "B"]},
        students=[{"id": "kid", "classes": {
            "1": "Math",
            "2": {"A": {"title": "Chem", "location": "Lab"}, "B": "Art"},
        }}],
    )
    cal = roundtrip(build_student_feed(s, s.students[0], STAMP))
    mon = [e for e in cal.walk("VEVENT") if e["DTSTART"].dt.date() == dt.date(2026, 9, 7)]
    tue = [e for e in cal.walk("VEVENT") if e["DTSTART"].dt.date() == dt.date(2026, 9, 8)]
    assert [str(e["SUMMARY"]) for e in mon] == ["Math", "Chem"]
    assert str(mon[1]["LOCATION"]) == "Lab"
    assert [str(e["SUMMARY"]) for e in tue] == ["Math", "Art"]


def test_example_school_builds_every_feed():
    s = load_school(EXAMPLE)
    for f in s.feeds:
        assert roundtrip(build_feed(s, f, STAMP)).walk("VEVENT"), f.id
    for st in s.students:
        assert roundtrip(build_student_feed(s, st, STAMP)).walk("VEVENT")
