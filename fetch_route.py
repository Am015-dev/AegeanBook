"""Fetch routes on request, on GitHub's servers (run by .github/workflows/fetch-route.yml when the Flights page's Update prices is
pressed). Google rate-limits Streamlit Cloud's shared server, so the app asks GitHub to do the searching. Writes data/routes.csv
(the same columns as data/daily.csv), which the app reads from GitHub.

    python fetch_route.py "ATH,BIO,2026-11-01,2026-11-30;BIO,ATH,2026-11-03,2026-12-07" 1 [new_rows.csv]
    python fetch_route.py --merge new_rows.csv   # add those rows to the current data/routes.csv (the workflow's save step)
    python fetch_route.py --refresh              # the daily run: fetch again every route searched in the last 60 days

AIRLINE=GQ (environment, or a fourth argument) fetches Sky Express instead, economy only, into data/skyexpress.csv (same columns; the
file is the airline). --merge and --refresh read AIRLINE too; --refresh keeps every tracked airline fresh. Each airline has its own
permanent history beside its file (data/history_prices.csv, data/history_skyexpress.csv): never trimmed, one row per fare change.
Searched routes are recorded in data/tracked_routes.csv (orig,dest,nonstop,last_requested,from,to,airline) so the daily run can refresh
them: from and to are the union of the days ever requested, so a summer month planned on the Overview stays fresh until it is booked.
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
HISTORY = {"A3": "history_prices.csv", "GQ": "history_skyexpress.csv"}  # IATA code -> its permanent history, next to OUT
FIELDS = ["day", "orig", "dest", "cabin", "price", "source", "fetched", "nonstop"]
KEEP = 30  # days a fetch is kept in the file, as long as the app keeps it (db.RETENTION_DAYS)
TRACK_DAYS, TRACK_MAX, REFRESH_DAYS = 60, 20, 90  # --refresh: routes searched this recently, at most this many, this far ahead at least
HORIZON = 330  # a tracked range never reaches further: Google's calendar stops there (trip_plan.HORIZON, views/flights.py)


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


def log_history(rows, airline="A3"):
    """Add the rows to the airline's permanent history (never trimmed, one row per change, see history.py). The history is a bonus:
    a failure is printed, never raised, so it cannot break a fetch."""
    try:
        return history.append_changes(OUT.with_name(HISTORY[airline]), rows, ("day", "orig", "dest", "cabin", "nonstop"), ("price",))
    except Exception as e:
        print(f"  history: {type(e).__name__}: {e}")
        return 0


def track_routes(rows, path, airline="A3"):
    """Upsert the rows' distinct (orig, dest, nonstop, airline) into the tracked-routes file with last_requested = today and from,to
    widened to the rows' days (the union of every range requested, to at most today + HORIZON), so --refresh keeps a month planned far
    ahead fresh. Rows written before the airline column existed are AEGEAN's."""
    routes = {(r["orig"], r["dest"], r["nonstop"], r.get("airline") or "A3"): [r["last_requested"], r.get("from") or "", r.get("to") or ""]
              for r in read(path)} if path.exists() else {}
    far = (date.today() + timedelta(HORIZON)).isoformat()
    for r in rows:
        v = routes.setdefault((r["orig"], r["dest"], str(r["nonstop"]), airline), ["", "", ""])
        v[0] = date.today().isoformat()
        v[1], v[2] = min(filter(None, (v[1], r["day"]))), min(max(v[2], r["day"]), far)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["orig", "dest", "nonstop", "last_requested", "from", "to", "airline"])
        w.writerows((o, d, n, *v, a) for (o, d, n, a), v in sorted(routes.items()))


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
    """The daily run: fetch again the next REFRESH_DAYS, or up to the last day ever requested (`to`) when that is further, for every tracked
    route searched in the last TRACK_DAYS (the newest TRACK_MAX of them). The daily run's own legs get only the part beyond its DAYS, so a
    summer planned on ATH-FRA stays fresh too. The rows go to the permanent history as well, so recent weeks can be compared."""
    from collect_daily import DAYS, LEGS  # here: that module needs the hotel packages, which the fetch-route job does not install
    tracked = OUT.with_name("tracked_routes.csv")
    today = date.today()
    since = (today - timedelta(TRACK_DAYS)).isoformat()
    jobs = []  # (route, airline, first day, last day) for every route with something to fetch
    for r in read(tracked) if tracked.exists() else []:
        code = r.get("airline") or "A3"
        daily = code == "A3" and (r["orig"], r["dest"]) in LEGS  # collect_daily fetches these legs' first DAYS already
        a = today + timedelta(1 + (DAYS if daily else 0))
        b = today + timedelta(REFRESH_DAYS)
        if r.get("to"):
            b = max(b, min(date.fromisoformat(r["to"]), today + timedelta(HORIZON)))
        if r["last_requested"] >= since and a <= b:
            jobs.append((r, code, a, b))
    jobs.sort(key=lambda j: j[0]["last_requested"], reverse=True)
    for r, *_ in jobs[TRACK_MAX:]:  # ponytail: a fixed cap keeps the run under ~30 min; split into matrix jobs if more routes are wanted
        print(f"skipped {r['orig']}-{r['dest']} (last requested {r['last_requested']}, over {TRACK_MAX} routes)")
    got_by, failed = {"A3": []}, 0  # rows by airline: each airline is saved in its own file, with its own permanent history
    for r, code, a, b in jobs[:TRACK_MAX]:
        got, f = fetch(f"{r['orig']},{r['dest']},{a},{b}", r["nonstop"] not in ("0", "false", "False"), passes, pause, code)
        got_by[code], failed = got_by.get(code, []) + got, failed + f
    rows = []
    for code, got in got_by.items():
        save(got, airline=code)
        log_history(got, code)  # the permanent history next to each file: never trimmed, one row per change
        rows += got
    print(f"refreshed {min(len(jobs), TRACK_MAX)} routes: {len(rows)} saved, {failed} failed")
    return 0 if rows or not failed else 1


if __name__ == "__main__":
    airline = (sys.argv[4] if len(sys.argv) > 4 else os.environ.get("AIRLINE") or "A3").upper()
    if airline not in AIRLINES:
        sys.exit(f"unknown airline {airline}: one of {', '.join(AIRLINES)}")
    if sys.argv[1] == "--merge":
        rows = read(sys.argv[2])
        save(rows, track=True, airline=airline)
        log_history(rows, airline)  # every fetch from the app goes into the airline's permanent history at once, so no fare is ever lost
    elif sys.argv[1] == "--refresh":
        sys.exit(refresh())
    else:
        sys.exit(main(sys.argv[1], sys.argv[2] not in ("0", "false", "False"), new=sys.argv[3] if len(sys.argv) > 3 else None, airline=airline))
