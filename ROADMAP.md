# Future enhancements

Ideas and open items, roughly in priority order. Nothing here is built yet.

## 1. iPhone home-screen widget

**Goal:** a condensed version of the site's *Daily schedule → Kids* view
(one column per kid, the day's classes, lunches and carpool) on its own
home-screen page, rendered each morning. No browser to open.

**Approach:** [Scriptable](https://scriptable.app) (free) runs a small
JavaScript file that draws a native widget. It fetches the site's
`schedule.json` (already published), picks today's date, and draws the
timeline as an image, so it reuses the data the site uses.

**Notes:**
- **Size:** iPhone widgets come in small, medium and large; extra-large is
  iPad only. A large widget fills about two-thirds of a home screen page.
  To fill a whole page, stack a large widget (morning to early afternoon)
  and a medium one (the rest of the day, plus carpool), or put everything
  in one large widget at a smaller scale.
- **Refresh:** iOS decides when widgets refresh. A script can ask to
  refresh after, say, 5 am, and a daily update is well within what iOS
  allows. But it isn't a guaranteed exact time, and it won't update
  minute-by-minute. Show clock times ("till 11:03"), not countdowns.
- **Draw it like the Kids view:** one column per kid, color-coded by school
  (same palette as the site), with a "now" line drawn at render time. That
  line will drift, so it's optional.
- **Offline:** cache the last `schedule.json` in Scriptable's local storage
  so the widget still renders without a connection.

## 2. More calendars: kids' activities, and eventually everything

**Goal:** add other iCal subscription links (sports teams, lessons, clubs,
work shifts) so each kid's calendar and the site show *all* of their
commitments, not just school. Eventually, route the whole family calendar
through this.

**Approach:**
- A `data/sources.yaml` list of external `.ics` URLs, each tagged with the
  kid (or kids) it belongs to and a short label/color.
- A **scheduled** GitHub Action (e.g. hourly) fetches each URL, merges the
  events into the per-kid feeds and `schedule.json`, and redeploys.
  External calendars change on their own schedule, so this can't only run
  on push.
- The site gets an "activities" style (e.g. outlined blocks) so school
  periods and activities are easy to tell apart. The Today view's
  "free/done" logic should treat activities as busy.

**Things to decide first:**
- **Privacy:** this is the big one. The repo and the Pages site are public.
  Activity calendars often have secret subscription URLs, and they say
  where a kid will be outside school, which is more sensitive than a
  bell schedule. Options:
  1. Keep source URLs in GitHub Actions **secrets** (never in the repo),
     and publish only coarse info ("Soccer 4–5:30") with no locations.
  2. Move the site/feeds behind access control. GitHub Pages can't be
     made private without GitHub Enterprise. Cloudflare Pages + Access, or
     a small private server, can. This is the right home if this becomes
     the whole family calendar.
- **Edits flow:** external events are read-only here; changes happen in
  the source app (TeamSnap, etc.) and show up on the next fetch.
- **Recurring events and exceptions** (RRULE/EXDATE) need to be expanded
  correctly when merging. The `icalendar` library plus a recurrence helper
  handles this.

## 3. Driving assignments view

**Goal:** one page every driver can check to see who's driving what, when.

**What exists:** carpool runs in `data/carpool.yaml` (C/J and VB mornings;
Jenn and Aaron afternoons), a Carpool Calendar feed, and per-driver feeds
for Jenn and Aaron.

**Ideas:**
- A **Drivers** tab: a week grid (days across, morning/afternoon down)
  showing the driver initials for each run, with today highlighted. Tap a
  run to see stops, kids and times.
- Per-driver feeds for every driver (add C/J and VB), so each family can
  subscribe to only their own runs.
- **Swaps:** merge the shared iCloud "Carpool Changes" calendar (its public
  link) using the external-sources work in #2. A swap entered there would
  then override the scheduled driver on the site and in the feeds.
  Convention to agree on: an event titled "Aaron for Jenn" or "No carpool"
  on that date.
- Keep other families as initials (the site is public).

## 4. Smaller items

- **Carpool in each kid's own calendar:** currently pickups are only in the
  Carpool Calendar; add an option to include each kid's pickups in their
  School Calendar.
- **Assembly / special schedule days:** Jordan (60-minute assembly) and
  Brighton (gym, auditorium A/B) schedules are defined. Add dates as the
  schools announce them (one line each).

## Open data questions

- **Semester 1 end:** Jan 14, 2027 comes from third-party calendars;
  confirm against the official Canyons K-12 calendar. It controls when F's
  CTEC block ends and Jordan B days begin.
- **District day-off names** for Sep 28, Oct 26 and Feb 12, which are
  "No School" placeholders.
- **Semester 2 schedules** for D and R: Utah Studies 7, Health 7 and
  Digital Literacy look like semester courses.
- **Brighton:** Thursday early-out times (Sep 24, Feb 25) assume the
  Friday schedule. T3 APP is missing from L's and S's printouts. F's
  printout lists two T1 Period 4 classes (Physics and English 12).
- **F's Jordan class names**: shown as "Jordan Period 1/2/5/6".
- **Butler Elementary:** assumed to follow the K-12 district calendar;
  confirm any elementary-only days (K start days, early outs).
- **Real conflict on Feb 25:** Brighton early out moves F's Period 4 to
  10:31 while Jordan Period 2 runs until 11:03.
