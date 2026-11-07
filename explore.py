"""Explore: "where can I fly cheaply from Athens in December?" (Google Flights' Explore), answered from the fares saved for every route.
Pure functions over overview.legs(); the Overview shows them when To is Anywhere.
A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
from datetime import timedelta

import pandas as pd

import deals

VERSION = 3  # pages reload this module when Streamlit Cloud still has an older copy loaded (it keeps modules across redeploys)
NIGHTS = range(2, 8)  # a return of 2 to 7 nights, as the Overview's date grid
COLUMNS = ["dest", "price", "depart", "back", "nights", "days", "typical"]


def origins(legs):
    """Airports with fares saved from them, in the order of legs (the daily run's first). legs: {(orig, dest): {cabin: Series}}."""
    return list(dict.fromkeys(o for o, _ in legs))


def _between(s, start, end):
    """The fares of `s` (Series by iso day) on the days from `start` to `end`, as numbers."""
    s = pd.to_numeric(s, errors="coerce").dropna()
    d = pd.to_datetime(s.index)
    return s[(d >= pd.Timestamp(start)) & (d <= pd.Timestamp(end))]


def destinations(legs, orig, start, end, cabin, nights=None):
    """One row per destination with `cabin` fares saved from `orig` for the days `start` to `end` (dates), cheapest first: DataFrame with
    dest, price (the cheapest one-way, or with `nights` the cheapest return of that many nights, both ways added up), depart and back
    (dates; back None one way), nights, days (days with a flight in the period) and typical (the median price of the same kind of trip).
    nights: None for one way, else the nights away to consider (NIGHTS); a destination with no fares back (or no return trip of those nights)
    is then listed with its cheapest one-way, so a route searched one way still shows up."""
    rows = []
    for (o, d), f in legs.items():
        if o != orig or cabin not in f:
            continue
        out = _between(f[cabin], start, end)
        if not len(out):
            continue
        back = legs.get((d, o), {}).get(cabin)
        best = deals.best_trip(out, back if nights else None, start, end, nights or NIGHTS)  # a one-way when there is nothing back
        if best is None:
            continue
        if nights is None or best["back"] is None:
            typical = float(out.median())
        else:
            typical = float(deals.trip_grid(out, back, start, end, nights).stack().median())
        rows.append(dict(dest=d, price=best["price"], depart=best["depart"], back=best["back"], nights=best["nights"], days=len(out),
                         typical=typical))
    t = pd.DataFrame(rows, columns=COLUMNS)
    return t.sort_values(["price", "dest"], kind="stable").reset_index(drop=True)


WEEKEND = {(3, 3), (4, 2), (4, 3), (5, 1), (5, 2)}  # (departure weekday, nights): Thu/Fri/Sat out, Sun/Mon back, 1 to 3 nights


def weekends(legs, orig, cabin, today, weeks=8, ok=None):
    """Cheapest weekend away per destination over the next `weeks` weekends (Google Flights' weekend trips), from saved fares only:
    DataFrame with dest, price (both ways added up), depart, back (dates), nights, typical (the median of that destination's weekend trips),
    cheapest first. ok(dest): False skips a destination (a pair the route network says is not flown)."""
    end = pd.Timestamp(today + timedelta(weeks * 7))
    rows = []
    for (o, d), f in legs.items():
        back = legs.get((d, o), {}).get(cabin)
        if o != orig or cabin not in f or back is None or (ok and not ok(d)):
            continue
        out = _between(f[cabin], today + timedelta(1), end)
        out.index = pd.to_datetime(out.index)
        bk = pd.to_numeric(back, errors="coerce").dropna()
        bk.index = pd.to_datetime(bk.index)
        trips = [(out[a] + bk[a + pd.Timedelta(days=n)], a, n) for a in out.index for n in (1, 2, 3)
                 if (a.dayofweek, n) in WEEKEND and a + pd.Timedelta(days=n) in bk.index]
        if trips:
            price, a, n = min(trips)
            rows.append(dict(dest=d, price=float(price), depart=a.date(), back=(a + pd.Timedelta(days=n)).date(), nights=n,
                             typical=float(pd.Series([t[0] for t in trips]).median())))
    t = pd.DataFrame(rows, columns=["dest", "price", "depart", "back", "nights", "typical"])
    return t.sort_values(["price", "dest"], kind="stable").reset_index(drop=True)
