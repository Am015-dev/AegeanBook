"""Compare routes: the cheapest cash fare, the lowest award miles and the best day to spend miles on every route the app has data for, side by
side. Pure functions over the saved prices and award miles. A new module on purpose: Streamlit Cloud can run a new page against older, already
loaded modules, so nothing here is added to one."""
import html as _html
import sqlite3

import pandas as pd

KEYS = ["orig", "dest", "day", "cabin"]
STYLE = """<style>
.rb{background:#fff;border-radius:14px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 6px 20px rgba(0,0,0,.05);padding:16px 18px;margin:0 0 1rem}
.rb-h{font-weight:650;font-size:1.25rem;letter-spacing:-.01em;color:#1D1D1F;margin-bottom:6px}
.rb-r{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-top:1px solid #E5E5EA;font-size:.92rem;color:#1D1D1F}
.rb-r span:first-child{color:#6E6E73;white-space:nowrap}.rb-r span:last-child{text-align:right}
.rb-r small{color:#6E6E73;font-size:.82rem}
.rb-tag{display:inline-block;background:#E3F1EA;color:#0E7A53;border-radius:999px;padding:1px 8px;font-size:.72rem;font-weight:650;margin-left:6px}
</style>"""
EMPTY = pd.DataFrame(columns=KEYS + ["price"])


def load(db, start, end, nonstop):
    """(prices, miles): the newest saved recording of each day and cabin on every route, for the days start..end. prices has orig, dest, day,
    cabin, price; miles has orig, dest, day, cabin, miles, taxes (empty frames when nothing is saved; a database without the taxes column or
    without the award table, as Streamlit Cloud can still be running, gives fewer columns, not an error)."""
    args = (int(nonstop), start.isoformat(), end.isoformat())
    prices = pd.read_sql("SELECT orig, dest, day, cabin, price FROM prices WHERE nonstop=? AND day BETWEEN ? AND ? AND price IS NOT NULL "
                         "ORDER BY fetched", db, params=args)
    where = "FROM award_miles WHERE nonstop=? AND day BETWEEN ? AND ? ORDER BY fetched"
    try:
        try:
            miles = pd.read_sql(f"SELECT orig, dest, day, cabin, miles, taxes {where}", db, params=args)
        except (pd.errors.DatabaseError, sqlite3.Error):
            miles = pd.read_sql(f"SELECT orig, dest, day, cabin, miles {where}", db, params=args).assign(taxes=float("nan"))
    except (pd.errors.DatabaseError, sqlite3.Error):
        miles = pd.DataFrame(columns=KEYS + ["miles", "taxes"])
    return prices.drop_duplicates(KEYS, keep="last"), miles.drop_duplicates(KEYS, keep="last")


def summarise(prices, miles):
    """One row per route: route, orig, dest, then per cabin (econ_, biz_): cash (cheapest), cash_day, typical (median), miles (lowest), miles_day,
    seat_days (days with award seats), value (euros per 1,000 miles on the best day, the cash fare minus taxes over the miles) and value_day."""
    routes = sorted(set(zip(prices.orig, prices.dest)) | set(zip(miles.orig, miles.dest)))
    rows = []
    for o, d in routes:
        p, m = prices[(prices.orig == o) & (prices.dest == d)], miles[(miles.orig == o) & (miles.dest == d)]
        row = dict(route=f"{o} → {d}", orig=o, dest=d)
        for cabin, key in (("economy", "econ"), ("business", "biz")):
            cash = p[p.cabin == cabin].set_index("day").price.sort_index()  # by day, so a tie (the same price on several days) names the earliest
            seats = m[(m.cabin == cabin) & m.miles.notna()].set_index("day").sort_index()
            if len(cash):
                row.update({f"{key}_cash": float(cash.min()), f"{key}_cash_day": cash.idxmin(), f"{key}_typical": float(cash.median())})
            if len(seats):
                row.update({f"{key}_miles": float(seats.miles.min()), f"{key}_miles_day": seats.miles.idxmin(), f"{key}_seat_days": len(seats)})
            both = cash.to_frame("cash").join(seats[["miles", "taxes"]], how="inner") if len(cash) and len(seats) else None
            if both is not None and len(both):
                value = (both.cash - both.taxes.fillna(0)) / both.miles * 1000
                row.update({f"{key}_value": float(value.max()), f"{key}_value_day": value.idxmax()})
        rows.append(row)
    return pd.DataFrame(rows)


def winners(summary):
    """{column: route label} of the lowest econ_cash, biz_cash, econ_miles and biz_miles, and the highest econ_value and biz_value."""
    out = {}
    for col, best in (("econ_cash", "min"), ("biz_cash", "min"), ("econ_miles", "min"), ("biz_miles", "min"), ("econ_value", "max"), ("biz_value", "max")):
        if col in summary and summary[col].notna().any():
            out[col] = summary.loc[getattr(summary[col], "idx" + best)(), "route"]
    return out


def _day(iso):
    return f"{pd.Timestamp(iso):%a %d %b}"


def card_html(r, win):
    """One route's card. r: a row of summarise() as a dict; win: winners()."""
    e = _html.escape
    have = lambda k: k in r and pd.notna(r[k])
    tag = lambda col: '<span class="rb-tag">best</span>' if win.get(col) == r["route"] else ""
    row = lambda label, value: f'<div class="rb-r"><span>{e(label)}</span><span>{value}</span></div>'
    out = [f'<div class="rb"><div class="rb-h">{e(r["route"])}</div>']
    for label, key in (("Economy fare", "econ"), ("Business fare", "biz")):
        out.append(row(label, f'<b>€{r[key + "_cash"]:.0f}</b> {tag(key + "_cash")}<br><small>{_day(r[key + "_cash_day"])} · typical €{r[key + "_typical"]:.0f}</small>'
                       if have(key + "_cash") else "<small>no fare saved</small>"))
    for label, key in (("Economy award", "econ"), ("Business award", "biz")):
        if have(key + "_miles"):
            n = int(r[key + "_seat_days"])
            out.append(row(label, f'<b>{r[key + "_miles"]:,.0f} miles</b> {tag(key + "_miles")}<br><small>{_day(r[key + "_miles_day"])} · seats on {n} day{"s" if n != 1 else ""}</small>'))
        else:
            out.append(row(label, "<small>not collected yet</small>"))
    for label, key in (("Best day for miles", "econ"), ("", "biz")):
        if have(key + "_value"):
            out.append(row(label or "", f'{"Economy" if key == "econ" else "Business"} <b>€{r[key + "_value"]:.1f}</b> per 1,000 {tag(key + "_value")}<br>'
                                        f'<small>{_day(r[key + "_value_day"])}</small>'))
    out.append("</div>")
    return "".join(out)
