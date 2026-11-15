"""Command line: validate calendar data, inspect days, and build .ics feeds."""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

from .ical import build_feed, build_student_feed
from .model import CalendarError, load_school
from .resolve import resolve_days


def cmd_check(args) -> int:
    for path in args.files:
        school = load_school(path)
        days = resolve_days(school)
        session = [d for d in days if d.in_session]
        print(f"{school.name} ({school.id}) — {path}")
        print(f"  {school.first_day} → {school.last_day}: {len(session)} school days")
        by_sched = Counter(d.schedule.name for d in session)
        for name, n in by_sched.most_common():
            print(f"    {n:4d}  {name}")
        if school.rotation:
            by_rot = Counter(d.rotation for d in session if d.rotation)
            print("  rotation: " + ", ".join(f"{k}={v}" for k, v in sorted(by_rot.items())))
        print(f"  {len(school.events)} events, {len(school.overrides)} day overrides")
        print(f"  feeds: {', '.join(f.id for f in school.feeds) or '(none)'}")
        if school.students:
            print(f"  student feeds: {', '.join(s.id for s in school.students)}")
    return 0


def cmd_day(args) -> int:
    school = load_school(args.file)
    target = dt.date.fromisoformat(args.date)
    for d in resolve_days(school):
        if d.date != target:
            continue
        print(f"{d.date} ({d.date:%A})")
        if not d.in_session:
            print("  No school" + (f" — {', '.join(d.titles)}" if d.titles else ""))
            return 0
        print(f"  {d.schedule.name}" + (f" · {d.rotation} Day" if d.rotation else ""))
        for t in d.titles:
            print(f"  note: {t}")
        for p in d.schedule.periods:
            print(f"  {p.start:%H:%M}–{p.end:%H:%M}  {p.name}" + ("" if p.kind == "class" else f" ({p.kind})"))
        return 0
    print(f"{target} is outside {school.first_day}..{school.last_day}", file=sys.stderr)
    return 1


def cmd_build(args) -> int:
    out = Path(args.out)
    for path in args.files:
        school = load_school(path)
        days = resolve_days(school)
        dest = out / school.id
        dest.mkdir(parents=True, exist_ok=True)
        for feed in school.feeds:
            _write(dest / f"{feed.id}.ics", build_feed(school, feed, days=days))
        for student in school.students:
            _write(dest / f"{student.id}.ics", build_student_feed(school, student, days=days))
    return 0


def _write(path: Path, cal) -> None:
    path.write_bytes(cal.to_ical())
    print(f"wrote {path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sbtical", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="validate data files and print a summary")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("day", help="show how a single date resolves")
    p.add_argument("file")
    p.add_argument("date", help="YYYY-MM-DD")
    p.set_defaults(func=cmd_day)

    p = sub.add_parser("build", help="write .ics feeds")
    p.add_argument("files", nargs="+")
    p.add_argument("-o", "--out", default="public")
    p.set_defaults(func=cmd_build)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except CalendarError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
