"""The cheapest month to fly (Skyscanner's month view), from the fares the daily run saves. Pure functions.
A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import pandas as pd


def by_month(fares, min_days=7):
    """Cheapest fare per calendar month: Series indexed 'Nov 2026', ..., in date order; empty without fares. A month with fewer than
    `min_days` priced days (the edges of the 90 days) is left out: one day of a month is not that month's cheapest fare."""
    s = fares.dropna()
    if not len(s):
        return pd.Series(dtype=float)
    g = s.groupby(pd.to_datetime(s.index).to_period("M"))
    m = g.min()[g.count() >= min_days]
    return pd.Series(m.values, index=[p.strftime("%b %Y") for p in m.index], dtype=float)


def month_table(legs, cabin):
    """Routes as rows, months as columns, cheapest fare in each cell. legs: {(orig, dest): {cabin: Series}}."""
    rows = {f"{o} → {d}": by_month(f[cabin]) for (o, d), f in legs.items() if cabin in f and len(f[cabin])}
    if not rows:
        return pd.DataFrame()
    t = pd.DataFrame(rows).T
    order = sorted(t.columns, key=lambda c: pd.Period(pd.Timestamp("1 " + c), "M"))
    return t[order]


def month_colours(t, dear=0.15):
    """CSS per cell of month_table: each route's cheapest month green, months at least `dear` (15%) dearer than it orange."""
    def row(r):
        low = r.min()
        return ["background-color:#E3F4EA;color:#0E7A53;font-weight:650" if v == low else
                "background-color:#FDECE3;color:#C2410C" if pd.notna(v) and v >= low * (1 + dear) else "" for v in r]
    return t.apply(row, axis=1, result_type="expand").set_axis(t.columns, axis=1)
