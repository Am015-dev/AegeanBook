"""Fetch routes on request, on GitHub's servers (run by .github/workflows/fetch-route.yml when the Flights page's Update prices is
pressed). Google rate-limits Streamlit Cloud's shared server, so the app asks GitHub to do the searching. Writes data/routes.csv
(the same columns as data/daily.csv), which the app reads from GitHub.

    python fetch_route.py "ATH,BIO,2026-11-01,2026-11-30;BIO,ATH,2026-11-03,2026-12-07" 1 [new_rows.csv]
    python fetch_route.py --merge new_rows.csv   # add those rows to the current data/routes.csv (the workflow's save step)
    python fetch_route.py --refresh              # the daily run: fetch again every route searched in the last 60 days

AIRLINE=GQ (environment, or a fourth argument) fetches Sky Express instead, economy only, into data/skyexpress.csv (same columns; the
file is the airline). --merge and --refresh read AIRLINE too; --refresh keeps every tracked airline fresh.
Searched routes are recorded in data/tracked_routes.csv (orig,dest,nonstop,last_requested,airline) so the daily run can refresh them.
"""
import csv
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import history
from collector import Airline, fetch_range

OUT = Path(__file__).parent / "data" / "routes.csv"
AIRLINES = {"A3": "routes.csv", "GQ": "skyexpress.csv"}  # IATA code -> the file its fares live in, next to OUT
CABINS = {"A3": ("economy", "business"), "GQ": ("economy",)}  # Sky Express: economy only for now
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


def outfile(airline="A3"):
    """The file an airline's fares are saved in: OUT for AEGEAN, data/skyexpress.csv beside it for Sky Express."""
    return OUT.with_name(AIRLINES[airline])


def save(rows, path=None, track=False, airline="A3"):
    """Add the new rows to the file, one row per day, cabin and route (the newest); rows fetched more than KEEP days ago and past days
    are dropped. With track, the rows' routes are recorded as requested today (a search from the app, not a --refresh)."""
    path, keep = path or outfile(airline), []
    if track:
        track_routes(rows, path.with_name("tracked_routes.csv"), airline)
    if path.exists():
        with path.open() as f:
            old = (datetime.now() - timedelta(days=KEEP)).isoformat()
            keep = [r for r in csv.DictReader(f) if r["fetched"] >= old and r["day"] > date.today().isoformat()]
    newest = {(r["day"], r["orig"], r["dest"], r["cabin"], str(r["nonstop"])): r for r in keep + rows}  # a day fetched again: the new row
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(sorted(newest.values(), key=lambda r: (r["orig"], r["dest"], r["day"], r["cabin"])))


def track_routes(rows, path, airline="A3"):
    """Upsert the rows' distinct (orig, dest, nonstop) into the tracked-routes file with last_requested = today, for `airline`
    (rows written before the airline column existed are AEGEAN's)."""
    routes = {(r["orig"], r["dest"], r["nonstop"], r.get("airline") or "A3"): r["last_requested"] for r in read(path)} if path.exists() else {}
    for r in rows:
        routes[(r["orig"], r["dest"], str(r["nonstop"]), airline)] = date.today().isoformat()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["orig", "dest", "nonstop", "last_requested", "airline"])
        w.writerows((o, d, n, v, a) for (o, d, n, a), v in sorted(routes.items()))


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write_new(rows, path):
    """This fetch's rows on their own, so the save step can merge them into whatever data/routes.csv is newest when it saves."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)


def fetch(legs, nonstop, passes=3, pause=90, airline="A3"):
    """Fetch every leg; days that failed (usually a Google rate limit) are retried after a pause, like the daily run does.
    Returns (rows, days still failed). airline: A3 (both cabins) or GQ (Sky Express, economy only)."""
    now = datetime.now().isoformat(timespec="seconds")
    rows, failed = [], 0
    more = {} if airline == "A3" else dict(cabins=CABINS[airline], airline=Airline[airline])
    for o, d, a, b in parse(legs):
        done = set()
        for n in range(passes):
            failed = 0
            for day, cabin, price, source, status, err in fetch_range(a, b, o, d, skip=done, nonstop=nonstop, **more):
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


def main(legs, nonstop, passes=3, pause=90, new=None, airline="A3"):
    """A search from the app: fetch, save, and record the routes as requested today."""
    rows, failed = fetch(legs, nonstop, passes, pause, airline)
    save(rows, track=True, airline=airline)
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
    routes = [r for r in read(tracked) if r["last_requested"] >= since and ((r["orig"], r["dest"]) not in LEGS or (r.get("airline") or "A3") != "A3")] if tracked.exists() else []
    routes.sort(key=lambda r: r["last_requested"], reverse=True)
    for r in routes[TRACK_MAX:]:  # ponytail: a fixed cap keeps the run under ~30 min; split into matrix jobs if more routes are wanted
        print(f"skipped {r['orig']}-{r['dest']} (last requested {r['last_requested']}, over {TRACK_MAX} routes)")
    routes = routes[:TRACK_MAX]
    a, b = date.today() + timedelta(1), date.today() + timedelta(REFRESH_DAYS)
    rows, failed, other = [], 0, {}  # other: the rows of every other airline, by code, saved in their own files
    for r in routes:
        code = r.get("airline") or "A3"
        got, f = fetch(f"{r['orig']},{r['dest']},{a},{b}", r["nonstop"] not in ("0", "false", "False"), passes, pause, code)
        rows, failed = rows + (got if code == "A3" else []), failed + f
        if code != "A3":
            other[code] = other.get(code, []) + got
    save(rows)
    for code, got in other.items():
        save(got, airline=code)
    try:  # the permanent history next to the file: never trimmed, one row per change
        history.append_changes(OUT.with_name("history_prices.csv"), rows, ("day", "orig", "dest", "cabin", "nonstop"), ("price",))
    except Exception as e:  # the history is a bonus: never let it break the run
        print(f"  history: {type(e).__name__}: {e}")
    rows += [r for got in other.values() for r in got]
    print(f"refreshed {len(routes)} routes: {len(rows)} saved, {failed} failed")
    return 0 if rows or not failed else 1


if __name__ == "__main__":
    airline = (sys.argv[4] if len(sys.argv) > 4 else os.environ.get("AIRLINE") or "A3").upper()
    if airline not in AIRLINES:
        sys.exit(f"unknown airline {airline}: one of {', '.join(AIRLINES)}")
    if sys.argv[1] == "--merge":
        save(read(sys.argv[2]), track=True, airline=airline)
    elif sys.argv[1] == "--refresh":
        sys.exit(refresh())
    else:
        sys.exit(main(sys.argv[1], sys.argv[2] not in ("0", "false", "False"), new=sys.argv[3] if len(sys.argv) > 3 else None, airline=airline))
