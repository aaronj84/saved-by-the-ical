"""Static site for GitHub Pages: one JSON file plus a single page.

Kid data is derived from the same calendars as the .ics feeds, so the site and
subscribed calendars can't disagree. School data (periods per day) comes from
the same resolved days.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil

import yaml
from importlib import resources
from pathlib import Path

from .carpool import is_carpool_file, load_carpool, occurrences
from .combine import is_person_file, load_person
from .ical import build_feed, build_student_feed
from .model import School, load_school
from .resolve import resolve_days


def _add_calendar(days: dict, cal, school_id: str) -> None:
    def day(d: dt.date) -> dict:
        return days.setdefault(d.isoformat(), {"notes": [], "items": []})

    for ev in cal.walk("VEVENT"):
        start, end = ev["DTSTART"].dt, ev["DTEND"].dt
        title = str(ev["SUMMARY"])
        if isinstance(start, dt.datetime):
            busy = str(ev.get("TRANSP", "OPAQUE")) != "TRANSPARENT"
            day(start.date())["items"].append(
                [start.strftime("%H:%M"), end.strftime("%H:%M"), title, busy, school_id]
            )
        else:  # all-day: holidays, early outs, A/B labels (DTEND exclusive)
            d = start
            while d < end:
                day(d)["notes"].append(title)
                d += dt.timedelta(days=1)


def feed_catalog(files) -> list[dict]:
    """Every published feed: {path, name, description, group}, in file order."""
    out = []
    for path in files:
        if is_carpool_file(path):
            for f in load_carpool(path).feeds:
                out.append({"path": f"carpool/{f.id}", "name": f.name, "description": f.description,
                            "group": "Carpool"})
        elif is_person_file(path):
            person = load_person(path)
            out.append({"path": f"people/{person.id}", "name": person.name,
                        "description": person.description, "group": "Kids"})
        else:
            school = load_school(path)
            for f in school.feeds:
                out.append({"path": f"{school.id}/{f.id}", "name": f.calendar_name or f.name,
                            "description": f.description, "group": school.name})
            for st in school.students:
                if st.publish:
                    out.append({"path": f"{school.id}/{st.id}", "name": st.name,
                                "description": st.description, "group": "Kids"})
    return out


def split_subscriptions(catalog: list[dict], config: Path) -> tuple[list[dict], list[dict]]:
    """(main calendars in configured order, everything else)."""
    order = []
    if config.exists():
        order = (yaml.safe_load(config.read_text()) or {}).get("subscribe") or []
    by_path = {c["path"]: c for c in catalog}
    missing = [p for p in order if p not in by_path]
    if missing:
        raise ValueError(f"{config}: subscribe lists unknown feeds {missing}")
    main = [by_path[p] for p in order]
    rest = [c for c in catalog if c["path"] not in order]
    return main, rest


def _kid(kid_id, name, description, feed_path, parts) -> dict:
    days: dict[str, dict] = {}
    for school_id, cal in parts:
        _add_calendar(days, cal, school_id)
    for v in days.values():
        v["items"].sort()
        v["notes"] = sorted(set(v["notes"]), key=v["notes"].index)
    return {
        "id": kid_id,
        "name": name,
        # "D School Calendar" -> D (older names: "Class Schedule D")
        "initial": name.split()[0] if len(name.split()[0]) == 1 else name.split()[-1],
        "description": description,
        "feed": feed_path,
        "days": dict(sorted(days.items())),
    }


def _school(school: School) -> dict | None:
    """Bell schedules stored once; each day points at one with its A/B label."""
    if not any(s.periods for s in school.bell_schedules.values()):
        return None
    schedules = {}
    for sid, sched in school.bell_schedules.items():
        schedules[sid] = {
            "name": sched.name,
            "periods": [
                [p.start.strftime("%H:%M"), p.end.strftime("%H:%M"), p.name, p.kind,
                 p.group, dict(p.names) or None]
                for p in sched.periods
                if p.group != "unknown"  # placeholder track for kids with no lunch group yet
            ],
        }
    days = {}
    for d in resolve_days(school):
        if d.in_session:
            days[d.date.isoformat()] = [d.schedule.id, d.rotation, d.titles[0] if d.titles else None]
        elif d.titles:
            days[d.date.isoformat()] = [None, None, d.titles[0]]
    return {
        "id": school.id,
        "name": school.name,
        "short": school.short_name or school.name,
        "schedules": schedules,
        "days": days,
    }


def _columns(config: Path, loaded: dict[str, School]) -> list[dict]:
    """Schools-view columns from data/site.yaml (see that file)."""
    if not config.exists():
        return [{"title": s.short_name or s.name, "school": sid, "prefix": s.short_name or ""}
                for sid, s in loaded.items()]
    cols = []
    for c in (yaml.safe_load(config.read_text()) or {}).get("columns") or []:
        if c.get("carpool"):
            cols.append({"title": c["title"], "carpool": True})
            continue
        if c.get("school"):
            school = loaded[c["school"]]
            groups = sorted({st.group for st in school.students if st.group and st.group != "unknown"})
            cols.append({"title": c["title"], "school": school.id,
                         "prefix": c.get("prefix", school.short_name or ""), "groups": groups})
            continue
        days: dict[str, list] = {}
        for ref in c["feeds"]:
            sid, fid = ref.split("/")
            school = loaded[sid]
            feed = next(f for f in school.feeds if f.id == fid)
            for ev in build_feed(school, feed).walk("VEVENT"):
                start, end = ev["DTSTART"].dt, ev["DTEND"].dt
                if isinstance(start, dt.datetime):
                    days.setdefault(start.date().isoformat(), []).append(
                        [start.strftime("%H:%M"), end.strftime("%H:%M"), str(ev["SUMMARY"]),
                         str(ev.get("TRANSP", "OPAQUE")) != "TRANSPARENT", sid])
        for v in days.values():
            v.sort()
        cols.append({"title": c["title"], "label_from": c.get("label_from"),
                     "days": dict(sorted(days.items()))})
    return cols


def build_site(files: list[str], out: Path, feeds_dir: Path, config: Path | None = None) -> None:
    kids, feeds, schools = [], [], []
    loaded: dict[str, School] = {}
    carpool: dict[str, list] = {}
    for path in files:
        if is_carpool_file(path):
            cp = load_carpool(path)
            for date, start, end, rule in occurrences(cp):
                carpool.setdefault(date.isoformat(), []).append([
                    start.strftime("%H:%M"), end.strftime("%H:%M"), rule.short, rule.title,
                    rule.driver, list(rule.kids), rule.school.id])
            feeds.append({"school": "Carpool", "feeds": [
                {"name": f.name, "description": f.description, "path": f"feeds/carpool/{f.id}.ics"}
                for f in cp.feeds]})
            continue
        if is_person_file(path):
            person = load_person(path)
            parts = []
            for src in person.sources:
                days = resolve_days(src.school)
                if src.feed:
                    feed = next(f for f in src.school.feeds if f.id == src.feed)
                    parts.append((src.school.id, build_feed(src.school, feed, days=days)))
                else:
                    st = next(x for x in src.school.students if x.id == src.student)
                    parts.append((src.school.id, build_student_feed(src.school, st, days=days)))
            kids.append(_kid(person.id, person.name, person.description,
                             f"feeds/people/{person.id}.ics", parts))
            continue
        school = load_school(path)
        loaded[school.id] = school
        days = resolve_days(school)
        if school.feeds:
            feeds.append({"school": school.name, "feeds": [
                {"name": f.name, "description": f.description,
                 "path": f"feeds/{school.id}/{f.id}.ics"}
                for f in school.feeds
            ]})
        if (entry := _school(school)) is not None:
            schools.append(entry)
        for st in school.students:
            if st.publish:
                kids.append(_kid(st.id, st.name, st.description,
                                 f"feeds/{school.id}/{st.id}.ics",
                                 [(school.id, build_student_feed(school, st, days=days))]))
    config = config or Path("data/site.yaml")
    cfg = (yaml.safe_load(config.read_text()) or {}) if config.exists() else {}
    order = [str(i) for i in cfg.get("kids") or []]
    kids.sort(key=lambda k: (order.index(k["initial"]) if k["initial"] in order else len(order), k["name"]))
    order = ["butler-elementary", "butler", "jordan", "ctec", "brighton"]
    schools.sort(key=lambda s: (order.index(s["id"]) if s["id"] in order else 99, s["name"]))

    out.mkdir(parents=True, exist_ok=True)
    columns = _columns(config, loaded)
    main, rest = split_subscriptions(feed_catalog(files), config)
    data = {"timezone": "America/Denver", "kids": kids, "schools": schools,
            "columns": columns, "carpool": carpool,
            "subscribe": {"main": main, "more": rest}}
    (out / "schedule.json").write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
    assets = resources.files("sbtical") / "site_assets"
    for name in ("index.html", "icon.png"):
        src = assets / name
        if src.is_file():
            (out / name).write_bytes(src.read_bytes())
    if feeds_dir.exists():
        shutil.copytree(feeds_dir, out / "feeds", dirs_exist_ok=True)
    (out / ".nojekyll").write_text("")
    print(f"wrote site to {out} ({len(kids)} kids, {len(schools)} schools)")
