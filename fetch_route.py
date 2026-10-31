"""Fetch routes on request, on GitHub's servers (run by .github/workflows/fetch-route.yml when the Flights page's Update prices is
pressed). Google rate-limits Streamlit Cloud's shared server, so the app asks GitHub to do the searching. Writes data/routes.csv
(the same columns as data/daily.csv), which the app reads from GitHub.

    python fetch_route.py "ATH,BIO,2026-11-01,2026-11-30;BIO,ATH,2026-11-03,2026-12-07" 1 [new_rows.csv]
    python fetch_route.py --merge new_rows.csv   # add those rows to the current data/routes.csv (the workflow's save step)
    python fetch_route.py --refresh              # the daily run: fetch again every route searched in the last 60 days

Searched routes are recorded in data/tracked_routes.csv (orig,dest,nonstop,last_requested) so the daily run can refresh them.
"""
import csv
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import history
from collector import fetch_range

OUT = Path(__file__).parent / "data" / "routes.csv"
FIELDS = ["day", "orig", "dest", "cabin", "price", "source", "fetched", "nonstop"]
KEEP = 30  # days a fetch is kept in the file, as long as the app keeps it (db.RETENTION_DAYS)
TRACK_DAYS, TRACK_MAX, REFRESH_DAYS = 60, 20, 90  # --refresh: routes searched this recently, at most this many, this far ahead


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


def save(rows, path=None, track=False):
    """Add the new rows to the file, one row per day, cabin and route (the newest); rows fetched more than KEEP days ago and past days
    are dropped. With track, the rows' routes are recorded as requested today (a search from the app, not a --refresh)."""
    path, keep = path or OUT, []
    if track:
        track_routes(rows, path.with_name("tracked_routes.csv"))
    if path.exists():
        with path.open() as f:
            old = (datetime.now() - timedelta(days=KEEP)).isoformat()
            keep = [r for r in csv.DictReader(f) if r["fetched"] >= old and r["day"] > date.today().isoformat()]
    newest = {(r["day"], r["orig"], r["dest"], r["cabin"], str(r["nonstop"])): r for r in keep + rows}  # a day fetched again: the new row
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(sorted(newest.values(), key=lambda r: (r["orig"], r["dest"], r["day"], r["cabin"])))


def track_routes(rows, path):
    """Upsert the rows' distinct (orig, dest, nonstop) into the tracked-routes file with last_requested = today."""
    routes = {(r["orig"], r["dest"], r["nonstop"]): r["last_requested"] for r in read(path)} if path.exists() else {}
    for r in rows:
        routes[(r["orig"], r["dest"], str(r["nonstop"]))] = date.today().isoformat()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["orig", "dest", "nonstop", "last_requested"])
        w.writerows(k + (v,) for k, v in sorted(routes.items()))


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_new(rows, path):
    """This fetch's rows on their own, so the save step can merge them into whatever data/routes.csv is newest when it saves."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)


def fetch(legs, nonstop, passes=3, pause=90):
    """Fetch every leg; days that failed (usually a Google rate limit) are retried after a pause, like the daily run does.
    Returns (rows, days still failed)."""
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
    return rows, failed


def main(legs, nonstop, passes=3, pause=90, new=None):
    """A search from the app: fetch, save, and record the routes as requested today."""
    rows, failed = fetch(legs, nonstop, passes, pause)
    save(rows, track=True)
    if new:
        write_new(rows, new)
    print(f"{len(rows)} saved, {failed} failed")
    return 0 if rows or not failed else 1


def refresh(passes=3, pause=90):
    """The daily run: fetch the next REFRESH_DAYS again for every tracked route searched in the last TRACK_DAYS (the newest TRACK_MAX
    of them; the daily run's own legs are skipped), and add the rows to the permanent history so recent weeks can be compared."""
    from collect_daily import LEGS  # here: that module needs the hotel packages, which the fetch-route job does not install
    tracked = OUT.with_name("tracked_routes.csv")
    since = (date.today() - timedelta(TRACK_DAYS)).isoformat()
    routes = [r for r in read(tracked) if r["last_requested"] >= since and (r["orig"], r["dest"]) not in LEGS] if tracked.exists() else []
    routes.sort(key=lambda r: r["last_requested"], reverse=True)
    for r in routes[TRACK_MAX:]:  # ponytail: a fixed cap keeps the run under ~30 min; split into matrix jobs if more routes are wanted
        print(f"skipped {r['orig']}-{r['dest']} (last requested {r['last_requested']}, over {TRACK_MAX} routes)")
    routes = routes[:TRACK_MAX]
    a, b = date.today() + timedelta(1), date.today() + timedelta(REFRESH_DAYS)
    rows, failed = [], 0
    for r in routes:
        got, f = fetch(f"{r['orig']},{r['dest']},{a},{b}", r["nonstop"] not in ("0", "false", "False"), passes, pause)
        rows, failed = rows + got, failed + f
    save(rows)
    try:  # the permanent history next to the file: never trimmed, one row per change
        history.append_changes(OUT.with_name("history_prices.csv"), rows, ("day", "orig", "dest", "cabin", "nonstop"), ("price",))
    except Exception as e:  # the history is a bonus: never let it break the run
        print(f"  history: {type(e).__name__}: {e}")
    print(f"refreshed {len(routes)} routes: {len(rows)} saved, {failed} failed")
    return 0 if rows or not failed else 1


if __name__ == "__main__":
    if sys.argv[1] == "--merge":
        save(read(sys.argv[2]), track=True)
    elif sys.argv[1] == "--refresh":
        sys.exit(refresh())
    else:
        sys.exit(main(sys.argv[1], sys.argv[2] not in ("0", "false", "False"), new=sys.argv[3] if len(sys.argv) > 3 else None))
