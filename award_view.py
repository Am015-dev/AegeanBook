"""How collected AEGEAN award miles are shown: the headline numbers, a chart, and one tidy table. Kept out of ui.py on purpose: Streamlit
Cloud can run a new page against an older, already loaded ui.py, and a new module is always imported fresh."""
import sqlite3

import pandas as pd
import plotly.graph_objects as go

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
    if len(at) == len(s) and len(s) > 1:  # no need to say it twice
        return f"{int(low):,} miles", f"{when} · same on all {len(s)} days with seats"
    more = f" · {len(at)} days at this level" if len(at) > 1 else ""
    return f"{int(low):,} miles", f"{when}{more} · {len(s)} days with seats"


def miles_table(econ, biz):
    """One row per day that has award seats in at least one cabin; '—' where a cabin has none."""
    seats = pd.DataFrame({"economy": econ, "business": biz}).dropna(how="all")
    fmt = lambda v: f"{int(v):,}" if pd.notna(v) else "—"
    return pd.DataFrame({"Date": [f"{pd.Timestamp(d):%a %d %b}" for d in seats.index],
                         "Economy miles": seats.economy.map(fmt), "Business miles": seats.business.map(fmt)})


def miles_chart(econ, biz):
    """Lowest award miles per day; a day without seats is a gap, not a zero."""
    x = pd.to_datetime(econ.index)
    fig = go.Figure()
    for label, colour, y in (("Economy", ECONOMY, econ), ("Business", BUSINESS, biz)):
        fig.add_scatter(x=x, y=y, name=label, mode="lines+markers", connectgaps=False, line=dict(color=colour, width=2),
                        marker=dict(size=8, color=colour, line=dict(color="#fff", width=2)),
                        hovertemplate=f"{label}: %{{y:,.0f}} miles<extra></extra>")
    fig.update_layout(height=320, margin=dict(l=28, r=8, t=40, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(color=MUTED), hovermode="x unified", legend=dict(orientation="h", x=0, y=1.15, font=dict(color=INK)),
                      xaxis=dict(tickformat="%d %b", showgrid=False, linecolor=GRID),
                      yaxis=dict(ticksuffix=" mi", tickformat=",", automargin=True, gridcolor=GRID, zeroline=False, rangemode="tozero"))
    return fig


# --- booking strategy: cash or miles, one way and return ---------------------------------------------------------------------------------
import html as _html

STYLE = """<style>
.plan{background:#fff;border-radius:14px;box-shadow:0 1px 2px rgba(0,0,0,.04),0 6px 20px rgba(0,0,0,.05);padding:16px 18px;margin:0 0 1rem}
.plan-h{display:flex;align-items:center;gap:10px;font-weight:650;font-size:1.05rem;color:#1D1D1F;margin-bottom:6px}
.plan-h i{width:9px;height:9px;border-radius:50%;display:inline-block}
.plan-s{color:#6E6E73;font-size:.82rem;font-weight:600;margin:12px 0 4px}
.plan-r{display:flex;justify-content:space-between;gap:12px;padding:6px 0;border-top:1px solid #E5E5EA;font-size:.92rem;color:#1D1D1F}
.plan-r span:first-child{color:#6E6E73}.plan-r span:last-child{text-align:right}
.plan-v{margin:8px 0 0;font-size:.92rem;color:#1D1D1F;text-wrap:pretty}.plan-v b{font-weight:650}
.plan-v.miles b{color:#0E7A53}.plan-v.cash b{color:#2563C9}
</style>"""


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


def per_thousand(cash, miles, taxes=None):
    """Euros each 1,000 miles save: the cash fare, minus the cash taxes the award ticket still costs, per 1,000 miles. None when either
    is missing."""
    return (cash - (taxes or 0)) / miles * 1000 if cash and miles else None


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


def _day(iso):
    return f"{pd.Timestamp(iso):%a %d %b}"


def _line(prefix, cash, miles, taxes, threshold):
    """One verdict: the saving when `miles` (plus `taxes`) replace paying `cash`, against what you value your miles at."""
    v = per_thousand(cash, miles, taxes)
    good = v >= threshold
    cost = f"{miles:,.0f} miles + €{taxes:.0f} taxes" if taxes else f"{miles:,.0f} miles"
    return ("miles" if good else "cash",
            f"<b>{prefix}: {'use miles' if good else 'pay cash'}.</b> {cost} instead of €{cash:.0f} is €{v:.1f} per 1,000 miles, "
            f"{'above' if good else 'below'} your €{threshold:g}.")


def _verdicts(cash_min, miles, miles_when, threshold, taxes=None, best=None, best_when=""):
    """[(css class, html)]. First line: the dates are flexible, so the cheapest cash fare is set against the fewest miles. Second line: the
    best day to spend miles, the one where they replace the most euros (miles beat cash on a day when cash is dear). `best` is
    (miles, cash, taxes) of that day."""
    if not miles:
        return [("cash", "<b>Pay cash.</b> No award seats were found for these days.")]
    if not cash_min and not best:
        return [("miles", "<b>Miles only.</b> There is no cash price to compare with for these days.")]
    same = best is not None and cash_min is not None and best[0] == miles and abs(best[1] - cash_min) < 0.5
    out = []
    if cash_min:
        out.append(_line(f"On {best_when}" if same else "Flexible dates", cash_min, miles, taxes, threshold))
    if best and not same:
        out.append(_line(f"Best day for miles, {best_when}", best[1], best[0], best[2], threshold))
    return out


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


def plan_html(name, colour, one_way, ret, threshold):
    """One cabin's card: the one-way options and, when a return trip is being compared, the return options, each with its verdicts."""
    e = _html.escape
    row = lambda k, v: f'<div class="plan-r"><span>{e(k)}</span><span>{v}</span></div>'
    says = lambda lines: "".join(f'<p class="plan-v {cls}">{txt}</p>' for cls, txt in lines)
    plus = lambda taxes: f" + €{taxes:.0f} taxes" if taxes else ""
    out = [f'<div class="plan"><div class="plan-h"><i style="background:{colour}"></i>{e(name)}</div><div class="plan-s">One way</div>']
    cash_min = one_way["cash"][1] if "cash" in one_way else None
    if cash_min:
        out.append(row("Pay cash", f"<b>€{cash_min:.0f}</b> · {_day(one_way['cash'][0])}"))
    if "miles" in one_way:
        d, m, _, taxes = one_way["miles"]
        out.append(row("Use miles", f"<b>{m:,.0f} miles</b>{plus(taxes)} · {_day(d)}"))
        b = one_way.get("best")
        out.append(says(_verdicts(cash_min, m, _day(d), threshold, taxes, (b[1], b[2], b[3]) if b else None, _day(b[0]) if b else "")))
    else:
        out.append(says(_verdicts(None, None, "", threshold)))
    if ret is not None:
        out.append('<div class="plan-s">Return</div>')
        cash_min = ret["cash"][3] if "cash" in ret else None
        if cash_min:
            d, b, n, tot = ret["cash"]
            out.append(row("Pay cash", f"<b>€{tot:.0f}</b> · {_day(d)} → {_day(b)} · {n} nights"))
        if "miles" in ret:
            d, b, n, tot, _, taxes = ret["miles"]
            out.append(row("Use miles", f"<b>{tot:,.0f} miles</b>{plus(taxes)} · {_day(d)} → {_day(b)} · {n} nights"))
            bb = ret.get("best")
            out.append(says(_verdicts(cash_min, tot, "", threshold, taxes, (bb[3], bb[4], bb[5]) if bb else None,
                                      f"{_day(bb[0])} → {_day(bb[1])}" if bb else "")))
        else:
            out.append(says(_verdicts(None, None, "", threshold)))
    out.append("</div>")
    return "".join(out)
