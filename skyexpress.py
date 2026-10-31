"""Sky Express (IATA GQ), the Greek airline competing with AEGEAN, beside the AEGEAN data: its network (data/skyexpress_airports.csv,
refreshed by data/build_skyexpress_airports.py), its saved fares (the gq_prices table, filled from data/skyexpress.csv, which
fetch_route.py writes with AIRLINE=GQ) and the day-by-day comparison the Overview and Flights pages draw. Economy, nonstop, for now.
A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import csv
import html
import os
from functools import lru_cache

import pandas as pd

VERSION = 2  # pages reload this module when Streamlit Cloud still has an older copy loaded (it keeps modules across redeploys)
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
    """compare() as the page shows it: dated rows, euro cells, and which airline is cheaper that day by how much."""
    money = lambda v: "—" if pd.isna(v) else f"€{v:,.0f}"
    def cheaper(v):
        if pd.isna(v):
            return "—"
        return "Same price" if v == 0 else f"{NAME if v < 0 else 'AEGEAN'}, €{abs(v):,.0f} less"
    return pd.DataFrame({"AEGEAN": t.aegean.map(money).values, NAME: t.skyexpress.map(money).values, "Cheaper": t.difference.map(cheaper).values},
                        index=[f"{pd.Timestamp(d):%a %d %b}" for d in t.index])


def trip_price(out, back, depart, ret):
    """What an airline charges for exactly these dates: the one-way out on `depart` plus the one-way back on `ret` (None for a one-way);
    None when it has no fare for either day. out/back are iso-day -> euros series as cash_by_day returns them."""
    o = pd.to_numeric(out, errors="coerce").get(depart.isoformat()) if out is not None else None
    if o is None or pd.isna(o):
        return None
    if ret is None:
        return float(o)
    b = pd.to_numeric(back, errors="coerce").get(ret.isoformat()) if back is not None else None
    return None if b is None or pd.isna(b) else float(o + b)


def span(b):
    """A trip's dates in a few characters: "19–21 Nov", "28 Nov–2 Dec", "19 Nov" for a one-way."""
    if b["back"] is None:
        return f"{b['depart']:%-d %b}"
    if b["depart"].month == b["back"].month:
        return f"{b['depart']:%-d}–{b['back']:%-d %b}"
    return f"{b['depart']:%-d %b}–{b['back']:%-d %b}"


def head_to_head(a3, gq, a3_out, a3_back, gq_out, gq_back, pax=1):
    """The comparison a traveller can read in one look: a DataFrame with the airlines as columns and short rows: each airline's cheapest
    price, its dates, then both airlines priced on AEGEAN's dates and both on Sky Express's dates. a3/gq are deals.best_trip answers
    (None when an airline has no trip); the series are cash_by_day's. Cells are short text: "€159", "Thu 19 → Sat 21 Nov, 2 nights"."""
    money = lambda v: "no flight" if v is None else f"€{v * pax:,.0f}"
    def dates(b):
        if b is None:
            return "no fare saved"
        if b["back"] is None:
            return f"{b['depart']:%a %-d %b}, one way"
        return f"{b['depart']:%a %-d %b} → {b['back']:%a %-d %b}, {b['nights']} night{'s' if b['nights'] != 1 else ''}"
    rows = {"Cheapest trip": [money(a3["price"]) if a3 else "no fare saved", money(gq["price"]) if gq else "no fare saved"],
            "Dates": [dates(a3), dates(gq)]}
    for name, b, series in (("AEGEAN", a3, (gq_out, gq_back)), (NAME, gq, (a3_out, a3_back))):
        if b is not None:
            other = money(trip_price(*series, b["depart"], b["back"]))
            rows[f"Both on {span(b)}"] = [money(b["price"]), other] if name == "AEGEAN" else [other, money(b["price"])]
    return pd.DataFrame.from_dict(rows, orient="index", columns=["AEGEAN", NAME])


def html_table(df):
    """A DataFrame as a plain HTML table that wraps on a phone (st.dataframe clips long cells), class h2h; every cell escaped."""
    esc = lambda v: html.escape(str(v))
    head = "<tr><th></th>" + "".join(f"<th>{esc(c)}</th>" for c in df.columns) + "</tr>"
    body = "".join(f"<tr><th>{esc(i)}</th>" + "".join(f"<td>{esc(v)}</td>" for v in r) + "</tr>" for i, r in zip(df.index, df.values.tolist()))
    return f'<table class="h2h">{head}{body}</table>'


def verdict(a3, gq, a3_out, a3_back, gq_out, gq_back, pax=1):
    """One sentence that answers the question: which airline, when, and what the other costs on the same dates."""
    if a3 is None and gq is None:
        return "Neither airline has a fare saved for these days."
    if gq is None:
        return f"Only AEGEAN has fares saved for these days: €{a3['price'] * pax:,.0f}."
    if a3 is None:
        return f"Only {NAME} has fares saved for these days: €{gq['price'] * pax:,.0f}."
    cheap, dear = (("AEGEAN", a3), (NAME, gq)) if a3["price"] <= gq["price"] else ((NAME, gq), ("AEGEAN", a3))
    out, back = (gq_out, gq_back) if cheap[0] == "AEGEAN" else (a3_out, a3_back)
    same = trip_price(out, back, cheap[1]["depart"], cheap[1]["back"])
    when = span(cheap[1])
    text = f"Cheapest overall: {cheap[0]}, €{cheap[1]['price'] * pax:,.0f}, {when}"
    if a3["price"] == gq["price"]:
        text = f"Both airlines' cheapest trips cost €{a3['price'] * pax:,.0f}"
    if same is None:
        return text + f". {dear[0]} has no flight on those dates."
    diff = (same - cheap[1]["price"]) * pax
    return text + f". On the same dates {dear[0]} costs €{same * pax:,.0f}" + (f", €{diff:,.0f} more." if diff > 0 else ", the same." if diff == 0 else f", €{-diff:,.0f} less.")


def sentence(best, ret):
    """One line for the boarding pass stub: the cheapest Sky Express trip for the same filters, from deals.best_trip's answer."""
    if best is None:
        return f"{NAME}: no economy fares saved for these days"
    when = f"{best['depart']:%a %d %b}" + (f", {best['nights']} nights" if best["back"] is not None else "")
    kind = "" if (best["back"] is not None) == ret else (" one way" if best["back"] is None else " return")
    return f"{NAME}: €{best['price']:,.0f} on {when} (economy{kind})"
