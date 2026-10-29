"""How collected AEGEAN award miles are shown: the headline numbers, a chart, and one tidy table. Kept out of ui.py on purpose: Streamlit
Cloud can run a new page against an older, already loaded ui.py, and a new module is always imported fresh."""
import sqlite3

import pandas as pd

ECONOMY, BUSINESS, MUTED, INK, GRID = "#2563C9", "#B8741A", "#6E6E73", "#1D1D1F", "#E5E5EA"


def best_award(miles, collected):
    """(value, sub) for the lowest award miles of one cabin. miles: Series by iso day, NaN where the day has no award seats;
    collected: whether any award data exists for this route and range (no data is not the same as no seats)."""
    s = miles.dropna()
    if not len(s):
        return ("—", "No award seats on these days" if collected else "Not collected yet for this route")
    low = s.min()
    at = s[s == low]
    when = f"{pd.Timestamp(at.index[0]):%a %d %b}"
    if len(at) == len(s) and len(s) > 1:  # the same miles on every seat day: the first of them is not "the best day", so say how many instead
        return f"{int(low):,} miles", f"the same on each of {len(s)} days with seats, from {when}"
    more = f" · {len(at)} days at this level" if len(at) > 1 else ""
    return f"{int(low):,} miles", f"{when}{more} · {len(s)} days with seats"


def miles_table(econ, biz):
    """One row per day that has award seats in at least one cabin; '—' where a cabin has none."""
    seats = pd.DataFrame({"economy": econ, "business": biz}).dropna(how="all")
    fmt = lambda v: f"{int(v):,}" if pd.notna(v) else "—"
    return pd.DataFrame({"Date": [f"{pd.Timestamp(d):%a %d %b}" for d in seats.index],
                         "Economy miles": seats.economy.map(fmt), "Business miles": seats.business.map(fmt)})

# --- booking strategy: cash or miles, one way and return ---------------------------------------------------------------------------------



def load_award(db, orig, dest, nonstop, start, end):
    """(miles, taxes, last fetched) for one leg and date range. miles and taxes are frames indexed by iso day with economy and business
    columns, empty when nothing was collected. Survives an older database without the taxes column (Cloud can run this page against a
    db.py loaded before it existed) and one without the award table at all."""
    args = (orig, dest, int(nonstop), start.isoformat(), end.isoformat())
    where = "FROM award_miles WHERE orig=? AND dest=? AND nonstop=? AND day BETWEEN ? AND ? ORDER BY fetched"
    try:
        try:
            df = pd.read_sql(f"SELECT day, cabin, miles, taxes {where}", db, params=args)
        except (pd.errors.DatabaseError, sqlite3.Error):
            df = pd.read_sql(f"SELECT day, cabin, miles {where}", db, params=args).assign(taxes=float("nan"))
        last = db.execute("SELECT MAX(fetched) FROM award_miles WHERE orig=? AND dest=?", (orig, dest)).fetchone()[0]
    except (pd.errors.DatabaseError, sqlite3.Error):
        return pd.DataFrame(), pd.DataFrame(), None
    if not len(df):
        return pd.DataFrame(), pd.DataFrame(), last
    df = df.drop_duplicates(["day", "cabin"], keep="last")  # the newest recording of each day and cabin
    return df.pivot(index="day", columns="cabin", values="miles"), df.pivot(index="day", columns="cabin", values="taxes"), last


def _return_pairs(out_miles, back_miles, nights, out_taxes=None, back_taxes=None):
    """Every outbound + return pair with miles on both days: [(total miles, depart iso, back iso, nights, taxes or None)]. The Series are
    by iso day; taxes are only known when both legs have them."""
    pairs = []
    for d, m1 in out_miles.dropna().items():
        for n in nights:
            back = (pd.Timestamp(d) + pd.Timedelta(days=int(n))).strftime("%Y-%m-%d")
            m2 = back_miles.get(back)
            if m2 is None or pd.isna(m2):
                continue
            t1, t2 = (out_taxes.get(d) if out_taxes is not None else None), (back_taxes.get(back) if back_taxes is not None else None)
            pairs.append((float(m1 + m2), d, back, int(n), float(t1 + t2) if t1 is not None and t2 is not None and pd.notna(t1) and pd.notna(t2) else None))
    return pairs


def best_return_miles(out_miles, back_miles, nights, out_taxes=None, back_taxes=None):
    """The pair with the fewest miles in total: (total, depart iso, back iso, nights, taxes or None), or None."""
    pairs = _return_pairs(out_miles, back_miles, nights, out_taxes, back_taxes)
    return min(pairs, key=lambda p: p[0]) if pairs else None


def one_way_plan(view, cash_col, miles_col, taxes_col=None):
    """{'cash': (iso, price), 'miles': (iso, fewest miles, cash that day or None, taxes or None), 'best': (iso, miles, cash, taxes or None,
    euros per 1,000 miles)} from a day-indexed frame; 'best' is the day where miles replace the most cash. Missing keys when no data."""
    plan = {}
    c, m = view[cash_col].dropna(), view[miles_col].dropna()
    if len(c):
        plan["cash"] = (c.idxmin(), float(c.min()))
    if len(m):
        d = m.idxmin()
        day_cash = view[cash_col].get(d)
        tax = view[taxes_col].get(d) if taxes_col else None
        plan["miles"] = (d, float(m.min()), float(day_cash) if pd.notna(day_cash) else None, float(tax) if tax is not None and pd.notna(tax) else None)
        both = view[[cash_col, miles_col]].dropna()
        if len(both):
            taxes = view[taxes_col].reindex(both.index) if taxes_col else pd.Series(float("nan"), index=both.index)
            value = (both[cash_col] - taxes.fillna(0)) / both[miles_col] * 1000
            b = value.idxmax()
            plan["best"] = (b, float(both.loc[b, miles_col]), float(both.loc[b, cash_col]),
                            float(taxes[b]) if pd.notna(taxes[b]) else None, float(value.max()))
    return plan


def return_plan(trips_cabin, out_miles, back_miles, nights, out_taxes=None, back_taxes=None):
    """Same idea for a round trip. trips_cabin: that cabin's rows of round_trips (cheapest first); out_miles / back_miles (and the taxes):
    Series by day. 'miles' is the pair with the fewest miles, 'best' the pair where miles replace the most cash."""
    plan = {}
    cash_of = {}
    if len(trips_cabin):
        r = trips_cabin.iloc[0]
        plan["cash"] = (r.depart, r.back, int(r.nights), float(r.total))
        cash_of = {(t.depart, t.back): float(t.total) for t in trips_cabin.itertuples()}
    pairs = _return_pairs(out_miles, back_miles, nights, out_taxes, back_taxes) if len(nights) else []
    if pairs:
        tot, d, b, n, taxes = min(pairs, key=lambda p: p[0])
        plan["miles"] = (d, b, n, tot, cash_of.get((d, b)), taxes)
        scored = [((cash_of[(p[1], p[2])] - (p[4] or 0)) / p[0] * 1000, p) for p in pairs if (p[1], p[2]) in cash_of]
        if scored:
            v, p = max(scored, key=lambda s: s[0])
            plan["best"] = (p[1], p[2], p[3], p[0], cash_of[(p[1], p[2])], p[4], v)
    return plan
