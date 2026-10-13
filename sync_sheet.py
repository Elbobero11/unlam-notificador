#!/usr/bin/env python3
"""Load the events from a Google Sheet (published/shared as CSV) into events.json.

Only rows whose START date is after today are loaded (start > today), since reminders are sent
for events that haven't started yet. events.json is rewritten as:  the placeholder event + those
rows, sorted by date. Rows already in the past are simply not copied.

Sheet columns (first row = headers, any order, extra columns ignored, case-insensitive):
    section | activity | start | end | note | role
Spanish headers also work: seccion, actividad, inicio/desde, fin/hasta, nota, rol.
`end` may be empty (= same as start); `note` and `role` are optional.
Dates: 2026-10-03, 2026-10-3 or 03/10/2026 (day/month/year).

The CSV link comes from the environment variable SHEET_CSV_URL (a repository secret) and is never
printed. If the download fails, the answer isn't a CSV (e.g. a Google login page) or any row is
invalid, the script exits with an error and leaves events.json untouched.

Usage:
    SHEET_CSV_URL=... python sync_sheet.py --file events.json
    python sync_sheet.py --csv-file local.csv --file events.json --dry-run --today 2026-10-13
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

from notify import PLACEHOLDER

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
ENV = "SHEET_CSV_URL"

ALIASES = {
    "section": {"section", "seccion", "sección", "instancia", "bloque"},
    "activity": {"activity", "actividad", "evento", "nombre"},
    "start": {"start", "inicio", "desde", "fecha", "fecha inicio", "fecha de inicio"},
    "end": {"end", "fin", "hasta", "fecha fin", "fecha de fin"},
    "note": {"note", "nota", "notas", "detalle"},
    "role": {"role", "rol"},
}


class SheetError(ValueError):
    pass


def download(url, attempts=3):
    req = urllib.request.Request(url, headers={"User-Agent": "unlam-reminder-sync/1.0"})
    last = None
    for i in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
            return raw.decode("utf-8-sig", errors="replace"), ctype
        except urllib.error.HTTPError as err:
            last = f"HTTP {err.code}"
            if err.code in (400, 401, 403, 404):  # retrying won't help
                break
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last = type(err).__name__  # don't include the message: it may contain the URL
        time.sleep(2 * (i + 1))
    hint = " (is the sheet shared as 'anyone with the link can view', or published to the web?)" if last in (
        "HTTP 401", "HTTP 403", "HTTP 404") else ""
    raise SheetError(f"could not download the sheet: {last}{hint}")


def parse_sheet_date(value, where):
    text = str(value).strip()
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text)
        if not m:
            raise SheetError(f"{where}: can't read the date {value!r}. Use 2026-10-03 or 03/10/2026.")
        d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        raise SheetError(f"{where}: {value!r} is not a real date.") from None


def parse_csv(text, today):
    """Return the list of event dicts (start > today) found in the CSV text."""
    head = text.lstrip()[:200].lower()
    if head.startswith("<!doctype") or head.startswith("<html") or "<html" in head:
        raise SheetError(
            "the link returned a web page instead of a CSV (usually the sheet is not shared with "
            "'anyone with the link'). Check the sharing settings and that the link ends in output=csv "
            "or export?format=csv."
        )
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise SheetError("the sheet is empty (no header row).")
    headers = [h.strip().lower() for h in rows[0]]
    col = {}
    for field, names in ALIASES.items():
        for i, h in enumerate(headers):
            if h in names and field not in col:
                col[field] = i
    missing = [f for f in ("activity", "start") if f not in col]
    if missing:
        raise SheetError(
            f"the header row {rows[0]!r} has no column for: {', '.join(missing)}. "
            "Expected: section, activity, start, end, note, role."
        )

    def cell(row, field):
        i = col.get(field)
        return row[i].strip() if i is not None and i < len(row) else ""

    events = []
    for n, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue  # blank line
        where = f"sheet row {n}"
        activity = cell(row, "activity")
        if not activity:
            raise SheetError(f"{where}: the activity is empty.")
        start = parse_sheet_date(cell(row, "start"), where) if cell(row, "start") else None
        if start is None:
            raise SheetError(f"{where} ({activity!r}): the start date is empty.")
        end = parse_sheet_date(cell(row, "end"), where) if cell(row, "end") else start
        if end < start:
            raise SheetError(f"{where} ({activity!r}): the end date is before the start date.")
        if start <= today:
            continue
        ev = {
            "section": cell(row, "section"),
            "activity": activity,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "note": cell(row, "note"),
        }
        if cell(row, "role"):
            ev["role"] = cell(row, "role")
        events.append(ev)
    events.sort(key=lambda e: (e["start"], e["end"], e["activity"]))
    return events


def placeholder_item():
    return {
        "section": PLACEHOLDER["section"],
        "activity": PLACEHOLDER["activity"],
        "start": PLACEHOLDER["start"].isoformat(),
        "end": PLACEHOLDER["end"].isoformat(),
        "note": PLACEHOLDER["note"],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", default="events.json", help="events file to rewrite (default events.json)")
    ap.add_argument("--csv-file", help="read this local CSV instead of downloading (for tests)")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true", help="show the result without writing the file")
    args = ap.parse_args()

    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else datetime.now(TZ).date()
    try:
        if args.csv_file:
            with open(args.csv_file, encoding="utf-8-sig") as fh:
                text = fh.read()
        else:
            url = os.environ.get(ENV, "").strip()
            if not url:
                raise SheetError(f"the environment variable {ENV} is empty or missing (repository secret).")
            text, _ = download(url)
        events = parse_csv(text, today)
    except SheetError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)

    new = events + [placeholder_item()]
    try:
        with open(args.file, encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        old = None  # missing or broken file: it gets replaced with the sheet's content

    print(f"Sheet read: {len(events)} event(s) starting after {today.isoformat()}.")
    if old == new:
        print(f"{args.file} already matches the sheet. Nothing to do.")
        return
    if args.dry_run:
        print(json.dumps(new, ensure_ascii=False, indent=2))
        return
    with open(args.file, "w", encoding="utf-8") as fh:
        json.dump(new, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"{args.file} updated.")


if __name__ == "__main__":
    main()
