"""Sky Express (IATA GQ), the Greek airline competing with AEGEAN, beside the AEGEAN data: its network (data/skyexpress_airports.csv,
refreshed by data/build_skyexpress_airports.py), its saved fares (the gq_prices table, filled from data/skyexpress.csv, which
fetch_route.py writes with AIRLINE=GQ) and the day-by-day comparison the Overview and Flights pages draw. Economy, nonstop, for now.
A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import csv
import os
from functools import lru_cache

import pandas as pd

VERSION = 1  # pages reload this module when Streamlit Cloud still has an older copy loaded (it keeps modules across redeploys)
CODE, NAME, FILE, TABLE = "GQ", "Sky Express", "data/skyexpress.csv", "gq_prices"
COLOUR = "#C2255C"  # Sky Express's pink, dark enough for a line and a label on white


@lru_cache(maxsize=1)
def airports():
    """{IATA: dict(iata, city, country, seasonal)} for every airport Sky Express flies to now or seasonally."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "skyexpress_airports.csv"), encoding="utf-8") as f:
        return {r["iata"]: r for r in csv.DictReader(f)}


def serves(orig, dest):
    """Whether both airports are in Sky Express's network (it may still not fly them against each other: the fares will say)."""
    return orig in airports() and dest in airports() and orig != dest


def cash_by_day(db, orig, dest, start, end):
    """The newest saved Sky Express economy nonstop fare of each day (iso day -> euros); empty when there are none."""
    df = pd.read_sql(f"SELECT day, price FROM {TABLE} WHERE orig=? AND dest=? AND cabin='economy' AND nonstop=1 AND price IS NOT NULL "
                     "AND day BETWEEN ? AND ? ORDER BY fetched", db, params=(orig, dest, start.isoformat(), end.isoformat()))
    return df.drop_duplicates("day", keep="last").set_index("day").price.sort_index() if len(df) else pd.Series(dtype=float)


def saved_days(db, orig, dest, start, end):
    """Iso days with a Sky Express answer saved (priced or no flight) for this route and range; a fetch skips them."""
    return {r[0] for r in db.execute(f"SELECT DISTINCT day FROM {TABLE} WHERE orig=? AND dest=? AND nonstop=1 AND day BETWEEN ? AND ?",
                                     (orig, dest, start.isoformat(), end.isoformat()))}


def compare(a3, gq):
    """Both airlines' fares side by side: DataFrame indexed by iso day with aegean, skyexpress and difference (Sky Express minus AEGEAN,
    so a negative number is a Sky Express saving), NaN where one has no fare; days with neither are left out."""
    t = pd.DataFrame({"aegean": pd.to_numeric(a3, errors="coerce"), "skyexpress": pd.to_numeric(gq, errors="coerce")}).dropna(how="all")
    t["difference"] = t.skyexpress - t.aegean
    return t.sort_index()


def table(t):
    """compare() as the page shows it: dated rows, euro cells, an em dash where an airline has no fare."""
    money = lambda v: "—" if pd.isna(v) else f"€{v:,.0f}"
    diff = lambda v: "—" if pd.isna(v) else f"{'+' if v > 0 else '−' if v < 0 else ''}€{abs(v):,.0f}"
    return pd.DataFrame({"AEGEAN": t.aegean.map(money).values, NAME: t.skyexpress.map(money).values,
                         "Sky Express − AEGEAN": t.difference.map(diff).values}, index=[f"{pd.Timestamp(d):%a %d %b}" for d in t.index])


def sentence(best, ret):
    """One line for the boarding pass stub: the cheapest Sky Express trip for the same filters, from deals.best_trip's answer."""
    if best is None:
        return f"{NAME}: no economy fares saved for these days"
    when = f"{best['depart']:%a %d %b}" + (f", {best['nights']} nights" if best["back"] is not None else "")
    kind = "" if (best["back"] is not None) == ret else (" one way" if best["back"] is None else " return")
    return f"{NAME}: €{best['price']:,.0f} on {when} (economy{kind})"
