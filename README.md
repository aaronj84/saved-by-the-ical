# saved-by-the-ical

Store detailed school calendar data — bell schedules, day types, A/B rotations,
holidays, events — and publish it as iCal (`.ics`) subscription feeds.

## How it works

```
documents (PDFs, bell schedules, district calendars)
        │  parsed into
        ▼
data/schools/<school>.yaml     ← single source of truth, reviewed in git
        │  sbtical build
        ▼
public/<school>/<feed>.ics     ← served at a stable URL; calendars subscribe
```

Each date in the school year is resolved in this order (later wins):

1. **weekly** default schedule for that weekday (missing weekday = no school)
2. **rotation** schedule for that day's label (e.g. B days use block schedule)
3. **days** overrides, in file order — so a single-day entry listed after a
   range carves an exception out of it

The rotation (A/B, 1–6, …) advances once per school day and skips days off.
Overrides can force a label (`rotation: A`) or hold it (`advance_rotation: false`).

## Feed components

| include      | what it emits                                                   |
|--------------|-----------------------------------------------------------------|
| `bells`      | one timed event per period per school day                      |
| `day_labels` | all-day "A Day", "B Day · Minimum Day", … on each school day    |
| `days`       | titled overrides: holidays, breaks, minimum days                |
| `events`     | school events (filter with `categories`)                        |

`students:` entries produce per-person feeds where period numbers are replaced by
that person's classes, optionally varying by rotation label.

Event UIDs are deterministic, so rebuilding a feed updates subscribers'
calendars in place instead of creating duplicates. Don't rename school, feed or
student `id`s once people are subscribed.

## Usage

```sh
pip install -e '.[dev]'
sbtical check data/schools/example-high.yaml        # validate + summary
sbtical day   data/schools/example-high.yaml 2026-12-18   # how one date resolves
sbtical build data/schools/*.yaml -o public         # write .ics feeds
pytest
```

See `data/schools/example-high.yaml` (fictional) for the full data format.
