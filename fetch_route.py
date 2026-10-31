"""Fetch routes on request, on GitHub's servers (run by .github/workflows/fetch-route.yml when the Flights page's Update prices is
pressed). Google rate-limits Streamlit Cloud's shared server, so the app asks GitHub to do the searching. Writes data/routes.csv
(the same columns as data/daily.csv), which the app reads from GitHub.

    python fetch_route.py "ATH,BIO,2026-11-01,2026-11-30;BIO,ATH,2026-11-03,2026-12-07" 1 [new_rows.csv]
    python fetch_route.py --merge new_rows.csv   # add those rows to the current data/routes.csv (the workflow's save step)
"""
import csv
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from collector import fetch_range

OUT = Path(__file__).parent / "data" / "routes.csv"
FIELDS = ["day", "orig", "dest", "cabin", "price", "source", "fetched", "nonstop"]
KEEP = 30  # days a fetch is kept in the file, as long as the app keeps it (db.RETENTION_DAYS)


def parse(legs):
    """[(orig, dest, start, end)] from "ATH,BIO,2026-11-01,2026-11-30;BIO,ATH,..."; airports are three letters, dates ISO."""
    out = []
    for leg in filter(None, legs.split(";")):
        o, d, a, b = [x.strip() for x in leg.split(",")]
        if not (len(o) == len(d) == 3 and o.isalpha() and d.isalpha()):
            raise ValueError(f"not an airport pair: {o} {d}")
        a, b = date.fromisoformat(a), date.fromisoformat(b)
        if not (0 <= (b - a).days <= 400):
            raise ValueError(f"bad date range: {a} to {b}")
        out.append((o.upper(), d.upper(), a, b))
    return out


def save(rows, path=None):
    """Add the new rows to the file, one row per day, cabin and route (the newest); rows fetched more than KEEP days ago and past days
    are dropped."""
    path, keep = path or OUT, []
    if path.exists():
        with path.open() as f:
            old = (datetime.now() - timedelta(days=KEEP)).isoformat()
            keep = [r for r in csv.DictReader(f) if r["fetched"] >= old and r["day"] > date.today().isoformat()]
    newest = {(r["day"], r["orig"], r["dest"], r["cabin"], str(r["nonstop"])): r for r in keep + rows}  # a day fetched again: the new row
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(sorted(newest.values(), key=lambda r: (r["orig"], r["dest"], r["day"], r["cabin"])))


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_new(rows, path):
    """This fetch's rows on their own, so the save step can merge them into whatever data/routes.csv is newest when it saves."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)


def main(legs, nonstop, passes=3, pause=90, new=None):
    """Fetch every leg; days that failed (usually a Google rate limit) are retried after a pause, like the daily run does."""
    now = datetime.now().isoformat(timespec="seconds")
    rows, failed = [], 0
    for o, d, a, b in parse(legs):
        done = set()
        for n in range(passes):
            failed = 0
            for day, cabin, price, source, status, err in fetch_range(a, b, o, d, skip=done, nonstop=nonstop):
                if status == "error":  # not saved, so the next pass (or the next fetch) tries again
                    failed += 1
                    continue
                done.add((day.isoformat(), cabin))
                rows.append(dict(day=day.isoformat(), orig=o, dest=d, cabin=cabin, price="" if price is None else price,
                                 source=source, fetched=now, nonstop=int(nonstop)))
            print(f"{o}-{d} {a}..{b} pass {n + 1}: {len(done)} saved, {failed} failed")
            if not failed or n == passes - 1:
                break
            time.sleep(pause)  # lets a Google rate limit cool down
    save(rows)
    if new:
        write_new(rows, new)
    print(f"{len(rows)} saved, {failed} failed")
    return 0 if rows or not failed else 1


if __name__ == "__main__":
    if sys.argv[1] == "--merge":
        save(read(sys.argv[2]))
    else:
        sys.exit(main(sys.argv[1], sys.argv[2] not in ("0", "false", "False"), new=sys.argv[3] if len(sys.argv) > 3 else None))
