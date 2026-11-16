import datetime as dt
from pathlib import Path

import pytest
from icalendar import Calendar

from sbtical.ical import build_feed, build_student_feed
from sbtical.model import CalendarError, load_school, parse_school
from sbtical.resolve import resolve_days

EXAMPLE = Path(__file__).parent.parent / "examples/example-high.yaml"
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


DATA = Path(__file__).parent.parent / "data/schools"


def test_jordan_rotation_matches_original_icloud_calendar():
    fixture = Path(__file__).parent / "fixtures/jordan-ab-2026-27.txt"
    expected = {}
    for line in fixture.read_text().splitlines():
        if line and not line.startswith("#"):
            d, label = line.split()
            expected[dt.date.fromisoformat(d)] = label
    days = resolve_days(load_school(DATA / "jordan.yaml"))
    actual = {d.date: d.rotation for d in days if d.in_session}
    assert actual == expected


def test_all_real_schools_build():
    for path in sorted(DATA.glob("*.yaml")):
        s = load_school(path)
        for f in s.feeds:
            build_feed(s, f).to_ical()


def test_extends_merges_district_days_before_school_days(tmp_path):
    (tmp_path / "d.yaml").write_text(
        "school: {timezone: America/Denver}\n"
        "year: {first_day: 2026-09-07, last_day: 2026-09-11}\n"
        "days: [{start: 2026-09-07, end: 2026-09-11, no_school: true}]\n"
    )
    (tmp_path / "s.yaml").write_text(
        "extends: d.yaml\nschool: {id: s, name: S}\n"
        "bell_schedules: {r: {periods: []}}\nweekly: {mon: r, tue: r, wed: r}\n"
        "days: [{date: 2026-09-09, schedule: r}]\n"
    )
    s = load_school(tmp_path / "s.yaml")
    assert s.timezone.key == "America/Denver"
    assert [d.date.day for d in resolve_days(s) if d.in_session] == [9]


def test_feed_rotation_filter_and_rotation_specific_period_names():
    s = base(
        rotation={"labels": ["A", "B"]},
        bell_schedules={"reg": {"periods": [
            {"id": "1", "name": "P1/5", "names": {"A": "P1", "B": "P5"}, "start": "08:00", "end": "09:00"},
            {"id": "x", "name": "Other", "start": "09:00", "end": "10:00"},
        ]}},
        feeds=[
            {"id": "a", "include": ["bells"], "rotation": ["A"], "periods": ["1"]},
            {"id": "all", "include": ["bells"], "periods": ["1"]},
        ],
    )
    a = roundtrip(build_feed(s, s.feeds[0], STAMP)).walk("VEVENT")
    assert {e["DTSTART"].dt.date() for e in a} == {
        dt.date(2026, 9, d) for d in (7, 9, 11, 15, 17)
    }
    assert {str(e["SUMMARY"]) for e in a} == {"P1"}
    both = roundtrip(build_feed(s, s.feeds[1], STAMP)).walk("VEVENT")
    assert {str(e["SUMMARY"]) for e in both} == {"P1", "P5"}


def test_rotation_filter_requires_known_labels():
    with pytest.raises(CalendarError, match="unknown rotation labels"):
        base(rotation={"labels": ["A", "B"]}, feeds=[{"id": "f", "include": ["bells"], "rotation": ["C"]}])


def test_jordan_and_ctec_alternate_without_overlap():
    jordan = load_school(DATA / "jordan.yaml")
    ctec = load_school(DATA / "ctec.yaml")
    j = {e["DTSTART"].dt.date() for e in build_feed(jordan, jordan.feeds[0]).walk("VEVENT")}
    c = {e["DTSTART"].dt.date() for e in build_feed(ctec, ctec.feeds[0]).walk("VEVENT")}
    assert len(j) == 87 and len(c) == 87 and not j & c
