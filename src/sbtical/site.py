"""Static 'today' site for GitHub Pages: one JSON file plus a single page.

The JSON is derived from the same calendars as the .ics feeds, so the site
and subscribed calendars can't disagree.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from importlib import resources
from pathlib import Path

from .combine import build_person_feed, is_person_file, load_person
from .ical import build_feed, build_student_feed
from .model import load_school
from .resolve import resolve_days


def _kid_entry(kid_id: str, name: str, description: str | None, feed_path: str, cal) -> dict:
    days: dict[str, dict] = {}

    def day(d: dt.date) -> dict:
        return days.setdefault(d.isoformat(), {"notes": [], "items": []})

    for ev in cal.walk("VEVENT"):
        start, end = ev["DTSTART"].dt, ev["DTEND"].dt
        title = str(ev["SUMMARY"])
        if isinstance(start, dt.datetime):
            busy = str(ev.get("TRANSP", "OPAQUE")) != "TRANSPARENT"
            day(start.date())["items"].append(
                [start.strftime("%H:%M"), end.strftime("%H:%M"), title, busy]
            )
        else:  # all-day: holidays, early outs, A/B labels (DTEND exclusive)
            d = start
            while d < end:
                day(d)["notes"].append(title)
                d += dt.timedelta(days=1)
    for v in days.values():
        v["items"].sort()
        v["notes"] = sorted(set(v["notes"]), key=v["notes"].index)
    return {
        "id": kid_id,
        "name": name,
        "initial": name.split()[-1] if name.startswith("Class Schedule") else name[:1],
        "description": description,
        "feed": feed_path,
        "days": dict(sorted(days.items())),
    }


def build_site(files: list[str], out: Path, feeds_dir: Path) -> None:
    kids, feeds = [], []
    for path in files:
        if is_person_file(path):
            person = load_person(path)
            kids.append(_kid_entry(
                person.id, person.name, person.description,
                f"feeds/people/{person.id}.ics", build_person_feed(person),
            ))
            continue
        school = load_school(path)
        days = resolve_days(school)
        group = {"school": school.name, "feeds": []}
        for f in school.feeds:
            group["feeds"].append({
                "name": f.name, "description": f.description,
                "path": f"feeds/{school.id}/{f.id}.ics",
            })
        feeds.append(group)
        for st in school.students:
            if st.publish:
                kids.append(_kid_entry(
                    st.id, st.name, st.description,
                    f"feeds/{school.id}/{st.id}.ics",
                    build_student_feed(school, st, days=days),
                ))
    kids.sort(key=lambda k: k["name"])

    out.mkdir(parents=True, exist_ok=True)
    data = {"timezone": "America/Denver", "kids": kids, "feeds": feeds}
    (out / "schedule.json").write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    assets = resources.files("sbtical") / "site_assets"
    for name in ("index.html", "icon.png"):
        src = assets / name
        if src.is_file():
            (out / name).write_bytes(src.read_bytes())
    if feeds_dir.exists():
        shutil.copytree(feeds_dir, out / "feeds", dirs_exist_ok=True)
    (out / ".nojekyll").write_text("")
    print(f"wrote site to {out} ({len(kids)} kids)")
