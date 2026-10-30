"""The cheapest month to fly (Skyscanner's month view), from the fares the daily run saves. Pure functions.
A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import pandas as pd

VERSION = 2  # pages reload this module when Streamlit Cloud still has an older copy loaded


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


def trip_grid(out, back, start, end, nights=range(2, 8)):
    """Google Flights' date grid for a return trip: outbound days from `start` to `end` (dates) as rows, nights away as columns, the two
    one-way fares added up in each cell (NaN when either day has no fare). out, back: Series of fares by iso day."""
    days = pd.date_range(start, end)
    out = pd.to_numeric(out, errors="coerce").set_axis(pd.to_datetime(out.index))  # award days without seats can come as None
    back = pd.to_numeric(back, errors="coerce").set_axis(pd.to_datetime(back.index))
    t = pd.DataFrame({f"{n} nights": [out.get(d, float("nan")) + back.get(d + pd.Timedelta(days=n), float("nan")) for d in days]
                      for n in nights}, index=[f"{d:%a %d %b}" for d in days])
    return t.dropna(how="all")


def grid_colours(t, near=0.10):
    """CSS per cell of trip_grid: the cheapest trip green, trips within `near` (10%) of it light green. When every trip costs the same
    (award miles often do), all of them are light green: the grid then shows which dates are possible, not which is cheapest."""
    low = t.min().min()
    if low == t.max().max():
        return t.apply(lambda c: ["background-color:#E3F4EA;color:#0E7A53" if pd.notna(v) else "" for v in c])
    return t.apply(lambda c: ["background-color:#0E7A53;color:#fff;font-weight:650" if v == low else
                              "background-color:#E3F4EA;color:#0E7A53" if pd.notna(v) and v <= low * (1 + near) else "" for v in c])


def styled(t, fmt, colours):
    """A Styler that shows `t` formatted by `fmt` with "—" where empty, coloured by `colours(t)` on the numbers. st.dataframe shows
    NaN as "None" whatever the Styler's na_rep says, so the cells are turned into text first."""
    text = t.apply(lambda c: c.map(lambda v: fmt(v) if pd.notna(v) else "—"))
    return text.style.apply(lambda _: colours(t), axis=None)
