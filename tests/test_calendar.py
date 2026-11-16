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


def _feed_dates(school_file, feed_id):
    s = load_school(DATA / school_file)
    feed = next(f for f in s.feeds if f.id == feed_id)
    return {e["DTSTART"].dt.date() for e in build_feed(s, feed).walk("VEVENT")}


def test_morning_feeds_cover_each_school_day_exactly_once():
    """Sem 1: Jordan on A days, CTEC on B days. Sem 2: Jordan on both."""
    jordan = _feed_dates("jordan.yaml", "mornings")
    ctec = _feed_dates("ctec.yaml", "am-b-days")
    school_days = {d.date for d in resolve_days(load_school(DATA / "jordan.yaml")) if d.in_session}
    assert not jordan & ctec
    assert jordan | ctec == school_days
    assert max(ctec) <= dt.date(2027, 1, 14)
    days = {d.date: d for d in resolve_days(load_school(DATA / "jordan.yaml"))}
    assert {days[d].rotation for d in ctec} == {"B"}
    assert {days[d].rotation for d in jordan if d < dt.date(2027, 1, 15)} == {"A"}


def test_butler_lunch_group_feeds_have_no_overlaps():
    s = load_school(DATA / "butler.yaml")
    for feed in s.feeds:
        evs = sorted(
            (e["DTSTART"].dt, e["DTEND"].dt) for e in build_feed(s, feed).walk("VEVENT")
        )
        for (a_start, a_end), (b_start, _) in zip(evs, evs[1:]):
            assert b_start >= a_end, (feed.id, a_start, b_start)
        per_day = 8 if feed.group == "B" else 7  # 6 periods + lunch; B splits period 4
        assert len(evs) == 174 * per_day, feed.id


def test_groups_only_overlap_across_different_groups():
    with pytest.raises(CalendarError, match="overlaps"):
        base(bell_schedules={"reg": {"periods": [
            {"name": "a", "group": "A", "start": "08:00", "end": "09:00"},
            {"name": "b", "group": "A", "start": "08:30", "end": "09:30"}]}})
    base(bell_schedules={"reg": {"periods": [
        {"name": "a", "group": "A", "start": "08:00", "end": "09:00"},
        {"name": "b", "group": "B", "start": "08:30", "end": "09:30"}]}})


def test_later_override_hides_district_day_off_title():
    s = base(
        days=[
            {"date": "2026-09-08", "no_school": True, "title": "District Day Off"},
            {"date": "2026-09-09", "no_school": True, "title": "Old Name"},
            {"date": "2026-09-08", "schedule": "reg"},  # school cancels the day off, untitled
            {"date": "2026-09-09", "no_school": True, "title": "New Name"},
        ],
        feeds=[{"id": "d", "include": ["days"]}],
    )
    evs = build_feed(s, s.feeds[0], STAMP).walk("VEVENT")
    assert [str(e["SUMMARY"]) for e in evs] == ["New Name"]


def test_brighton_differs_from_district_where_its_calendar_says():
    brighton = {d.date: d for d in resolve_days(load_school(DATA / "brighton.yaml"))}
    district = {d.date: d for d in resolve_days(load_school(DATA / "canyons-district.yaml"))}
    diff = sorted(d for d in district if brighton[d].in_session != district[d].in_session)
    assert diff == [dt.date(2026, 9, 28), dt.date(2026, 10, 26),
                    dt.date(2026, 11, 13), dt.date(2027, 4, 23)]
    assert brighton[dt.date(2026, 9, 24)].schedule.id == "early_out"
    assert brighton[dt.date(2027, 2, 25)].schedule.id == "early_out"
    assert brighton[dt.date(2026, 9, 24)].rotation is None


def _student_day(student_id, group, day):
    import dataclasses
    s = load_school(DATA / "butler.yaml")
    st = next(x for x in s.students if x.id == student_id)
    st = dataclasses.replace(st, group=group)
    evs = build_student_feed(s, st).walk("VEVENT")
    return [
        (e["DTSTART"].dt.strftime("%H:%M"), e["DTEND"].dt.strftime("%H:%M"), str(e["SUMMARY"]))
        for e in sorted(evs, key=lambda e: str(e["DTSTART"].dt))
        if isinstance(e["DTSTART"].dt, dt.datetime) and e["DTSTART"].dt.date() == day
    ]


def test_student_lunch_group_controls_period_4():
    mon = dt.date(2026, 11, 16)  # B day (matches the SIS "Current Day: B")
    unknown = _student_day("class-schedule-d", "unknown", mon)
    assert ("11:03", "12:42", "Integrated Science 7 STEM + Lunch") in unknown
    b = _student_day("class-schedule-d", "B", mon)
    assert [x for x in b if "11:00" < x[0] < "12:45"] == [
        ("11:08", "11:38", "Integrated Science 7 STEM"),
        ("11:38", "12:08", "Lunch"),
        ("12:12", "12:42", "Integrated Science 7 STEM"),
    ]
    assert b[0] == ("07:55", "08:53", "Health 7")  # B-day period 1


def test_kid_feeds_contain_no_teacher_or_room_details():
    s = load_school(DATA / "butler.yaml")
    for st in s.students:
        for classes in st.classes.values():
            for a in classes.values():
                assert a.teacher is None and a.location is None, st.id


def test_brighton_classes_switch_by_trimester():
    s = load_school(DATA / "brighton.yaml")
    st = next(x for x in s.students if x.id == "class-schedule-l")
    evs = build_student_feed(s, st).walk("VEVENT")

    def first_period(day):
        return next(
            str(e["SUMMARY"]) for e in evs
            if isinstance(e["DTSTART"].dt, dt.datetime)
            and e["DTSTART"].dt.date() == day and e["DTSTART"].dt.strftime("%H:%M") == "07:45"
        )

    assert first_period(dt.date(2026, 11, 12)) == "Chemistry (Honors)"  # last day of T1
    assert first_period(dt.date(2026, 11, 16)) == "AP European History"  # first day of T2
    # No APP in T3 on the printout.
    t3 = [str(e["SUMMARY"]) for e in evs if isinstance(e["DTSTART"].dt, dt.datetime)
          and e["DTSTART"].dt.date() == dt.date(2027, 3, 2)]
    assert "APP" not in t3 and "Lunch" in t3


def test_student_class_keys_are_validated():
    with pytest.raises(CalendarError, match="unknown keys"):
        base(terms={"T1": {"start": "2026-09-07", "end": "2026-09-18"}},
             students=[{"id": "k", "classes": {"1": {"T9": "Art"}}}])


def test_fynn_combined_feed_has_no_overlaps_and_spans_three_schools():
    from sbtical.combine import build_person_feed, load_person

    cal = build_person_feed(load_person(Path(__file__).parent.parent / "data/people/class-schedule-f.yaml"))
    timed = sorted(
        (e["DTSTART"].dt, e["DTEND"].dt, str(e["SUMMARY"]))
        for e in cal.walk("VEVENT") if isinstance(e["DTSTART"].dt, dt.datetime)
    )
    overlaps = {a[0].date() for a, b in zip(timed, timed[1:]) if b[0] < a[1]}
    # Real conflict: Brighton early out (P4 at 10:31) vs Jordan Period 2 (to 11:03).
    assert overlaps == {dt.date(2027, 2, 25)}

    def on(day):
        return [(s.strftime("%H:%M"), t) for s, _, t in timed if s.date() == day]

    assert on(dt.date(2026, 11, 16)) == [  # B day, semester 1, T2
        ("07:30", "CTEC AM Class"), ("12:17", "US History"), ("13:24", "English 12")]
    assert on(dt.date(2027, 1, 20))[0] == ("07:45", "Jordan Period 5")  # B day, semester 2
    assert on(dt.date(2026, 9, 28)) == [  # K-12 day off, Brighton in session
        ("12:17", "Physics / English 12"), ("13:24", "Financial Literacy")]
    assert len({str(e["UID"]) for e in cal.walk("VEVENT")}) == len(cal.walk("VEVENT"))


def test_site_json_marks_lunch_free_and_lists_all_kids(tmp_path):
    import json

    from sbtical.site import build_site

    files = sorted(str(p) for p in DATA.glob("*.yaml")) + sorted(
        str(p) for p in (DATA.parent / "people").glob("*.yaml"))
    build_site(files, tmp_path, tmp_path / "nofeeds")
    data = json.loads((tmp_path / "schedule.json").read_text())
    kids = {k["initial"]: k for k in data["kids"]}
    assert sorted(kids) == ["D", "F", "L", "R", "S"]
    l_mon = kids["L"]["days"]["2026-11-16"]["items"]
    assert ["11:36", "12:11", "Lunch", False] in l_mon
    assert ["07:45", "08:46", "AP European History", True] in l_mon
    assert kids["D"]["days"]["2026-11-16"]["notes"] == ["Butler B Day"]
    assert "2026-11-26" in kids["F"]["days"]  # holiday note present
    assert (tmp_path / "index.html").exists() and (tmp_path / "icon.png").exists()
