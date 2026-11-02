"""Which airport pairs AEGEAN (A3) and Sky Express (GQ) actually fly, so the app never offers, or spends a search on, a pair with no
flight. Run on any computer with the project's requirements installed, never on GitHub Actions (a whole run is about 4,100 pairs and
two to three hours: about 170 GitHub minutes there, nothing on your own machine). Each pair is searched with Google Flights' date grid,
the same search the fares use, over the next 60 days and, if nothing, next summer's 60 days. Results go to data/network.csv, one row per
airline and pair (airports in alphabetical order: a pair flown one way is flown back); commit and push it and the app uses it.

Only pairs with an airport in Greece or Cyprus are checked: both airlines fly from there, never between two foreign airports, so a pair
like Frankfurt-Istanbul is "no flight" without asking.

    python network.py --local          # every pair not checked in the last 60 days, both airlines; saves as it goes, so it can stop
    python network.py --local A3       # AEGEAN only (or GQ)                                                      and start again
"""
import csv
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path(__file__).parent / "data"
OUT = DATA / "network.csv"
FIELDS = ["airline", "a", "b", "flies", "days", "first", "checked"]
HOME = ("Greece", "Cyprus")
JOBS = {"A3": None, "GQ": None}  # the airlines checked, in this order
AIRPORTS = {"A3": DATA / "aegean_airports.csv", "GQ": DATA / "skyexpress_airports.csv"}


def airports(airline):
    with open(AIRPORTS[airline], encoding="utf-8") as f:
        return [(r["iata"], r["country"]) for r in csv.DictReader(f)]


def pairs(airline):
    """Every pair (a, b), a < b, with an airport in Greece or Cyprus."""
    ap = airports(airline)
    return sorted({tuple(sorted((x, y))) for x, cx in ap for y, cy in ap if x != y and (cx in HOME or cy in HOME)})


def windows(today):
    """The next 60 days, then next summer's (20 Jun to 18 Aug) when it starts more than 60 days ahead and within Google's 330 days."""
    out = [(today + timedelta(1), today + timedelta(60))]
    summer = date(today.year + (today >= date(today.year, 6, 20)), 6, 20)
    if (summer - today).days > 60 and (summer - today).days + 60 <= 330:
        out.append((summer, summer + timedelta(59)))
    return out


def check(airline, a, b, today, fetch=None, pause=1.0):
    """dict row for one pair: flies 1 with the days found and the first one, flies 0 when every window answered with none, or None when
    Google did not answer (nothing is saved then, so the next run asks again)."""
    if fetch is None:
        import collector
        from fli.models import Airline
        fetch = lambda o, d, s, e: collector.fetch_cabin(o, d, "economy", s, e, airline=Airline[airline])
    for s, e in windows(today):
        got = None
        for n in range(2):
            try:
                got = fetch(a, b, s, e)
                break
            except Exception as err:
                print(f"  {a}-{b} {s}: {type(err).__name__}: {err}", flush=True)
                time.sleep(pause * 20 * (n + 1))
        if got is None:
            return None
        time.sleep(pause)
        if got:
            return dict(airline=airline, a=a, b=b, flies=1, days=len(got), first=min(got), checked=today.isoformat())
    return dict(airline=airline, a=a, b=b, flies=0, days=0, first="", checked=today.isoformat())


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save(rows, path=None):
    """Merge rows into the file: the newest row per airline and pair."""
    path = Path(path or OUT)
    key = lambda r: (r["airline"], r["a"], r["b"])
    newest = {key(r): r for r in (read(path) if path.exists() else []) + rows}
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(newest.values(), key=key))


# --- for the app ------------------------------------------------------------------------------------------------------------------------------
def load(path=None):
    """{(airline, a, b): True|False} from data/network.csv (empty when the check has not run yet)."""
    path = Path(path or OUT)
    return {(r["airline"], r["a"], r["b"]): r["flies"] == "1" for r in read(path)} if path.exists() else {}


def flies(net, airline, orig, dest, home=None):
    """True or False when known, None when the pair has not been checked yet. A pair with no airport in Greece or Cyprus is False:
    `home` is the set of those airports (the check never asks about other pairs)."""
    if home is not None and orig not in home and dest not in home:
        return False
    return net.get((airline, *sorted((orig, dest))))


def current():
    """The network the app uses: the repository's newest copy (the check commits it), else the deployed one."""
    import gh
    return load(gh.data_file("data/network.csv") or OUT)


def home_airports(airline):
    return {i for i, c in airports(airline) if c in HOME}


if __name__ == "__main__":
    from par import parallel
    which = sys.argv[2:] or list(JOBS)
    today = date.today()
    done = {(r["airline"], r["a"], r["b"]) for r in (read(OUT) if OUT.exists() else [])
            if r["checked"] >= (today - timedelta(60)).isoformat()}  # checked lately: skipped, so a stopped run picks up where it was
    for al in which:
        todo = [p for p in pairs(al) if (al, *p) not in done]
        print(f"{al}: {len(todo)} pairs to check", flush=True)
        rows, n = [], 0
        # three pairs at a time, as the fare fetches do: about 2.5 s a pair
        for n, (pair, r, err) in enumerate(parallel(todo, lambda p: check(al, *p, today, pause=0.3), workers=3, retries=0), 1):
            if r:
                rows.append(r)
            if n % 50 == 0 or n == len(todo):
                save(rows)
                rows = []
                print(f"{al}: {n}/{len(todo)} checked, saved", flush=True)
        save(rows)
    net = load()
    print(f"done: {sum(net.values())} pairs flown, {sum(not v for v in net.values())} not, in {OUT}")
