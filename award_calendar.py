"""Award availability calendar: one month grid per month in the range, every day showing its cash fare and whether an award seat exists. The
miles price of a route and cabin is fixed, so the useful question is not 'how many miles' but 'which days have a seat, and on which of them is
cash dearest' (those are the best days to spend miles). A new module on purpose: Streamlit Cloud can run a new page against older, already
loaded modules."""
import calendar
import html as _html
from datetime import date, timedelta

import pandas as pd

STYLE = """<style>
.cal{margin:0 0 1.2rem}
.cal-m{font-weight:650;font-size:1.05rem;color:#1D1D1F;margin:.2rem 0 .5rem}
.cal-g{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:6px}
.cal-w{color:#6E6E73;font-size:.75rem;font-weight:600;text-align:center;padding-bottom:2px}
.cal-c{background:#fff;border:1px solid #E5E5EA;border-radius:10px;padding:5px 2px 6px;min-height:58px;text-align:center;color:#6E6E73}
.cal-c small{display:block;font-size:.7rem;line-height:1.1}
.cal-c b{display:block;font-size:.92rem;font-weight:600;color:#1D1D1F;margin-top:2px}
.cal-c i{display:block;font-style:normal;font-size:.62rem;font-weight:650;letter-spacing:.02em;margin-top:1px}
.cal-c.cal-seat{background:#E3F1EA;border-color:#9ED2B8}.cal-c.cal-seat i{color:#0E7A53}
.cal-c.cal-best{background:#BFE3D0;border-color:#0E7A53}.cal-c.cal-best b{color:#0A5C3D}.cal-c.cal-best i{color:#0A5C3D}
.cal-c.cal-unk{background:#F5F5F7;border-style:dashed}.cal-c.cal-unk b{color:#6E6E73}
.cal-c.cal-none b{color:#AEAEB2}
.cal-k{display:flex;flex-wrap:wrap;gap:14px;font-size:.8rem;color:#6E6E73;margin:.4rem 0 1rem}
.cal-k span:before{content:"";display:inline-block;width:12px;height:12px;border-radius:4px;border:1px solid #E5E5EA;margin-right:6px;vertical-align:-2px}
.cal-k .k-seat:before{background:#E3F1EA;border-color:#9ED2B8}.cal-k .k-best:before{background:#BFE3D0;border-color:#0E7A53}
.cal-k .k-none:before{background:#fff}.cal-k .k-unk:before{background:#F5F5F7;border-style:dashed}
</style>"""
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def cells(cash, miles, checked, start, end):
    """One dict per day from start to end: {'day': date, 'state': 'best' | 'seat' | 'none' | 'unk', 'cash': price or None, 'miles': miles or None}.
    cash and miles are Series by iso day (miles NaN where the day was checked and has no award seat); checked is the set of iso days the
    award search covered, so a day it never looked at is 'unk' (no data), not 'none' (no seat). 'best' marks the seat days where cash is
    dearest (the top quarter of the cash fares on seat days): the days where spending miles replaces the most money."""
    days = [start + timedelta(i) for i in range((end - start).days + 1)]
    seat_cash = [float(cash.get(d.isoformat())) for d in days
                 if pd.notna(miles.get(d.isoformat())) and pd.notna(cash.get(d.isoformat()))]
    cut = pd.Series(seat_cash).quantile(0.75) if seat_cash else None
    out = []
    for d in days:
        iso = d.isoformat()
        c, m = cash.get(iso), miles.get(iso)
        c, m = (float(c) if c is not None and pd.notna(c) else None), (float(m) if m is not None and pd.notna(m) else None)
        if m is not None:
            state = "best" if c is not None and cut is not None and c >= cut else "seat"
        else:
            state = "none" if iso in checked else "unk"
        out.append(dict(day=d, state=state, cash=c, miles=m))
    return out


def calendar_html(cells_):
    """The month grids (Monday first) for the days in `cells_`, plus a legend. Only days in the range are drawn."""
    e = _html.escape
    if not cells_:
        return ""
    by_month = {}
    for c in cells_:
        by_month.setdefault((c["day"].year, c["day"].month), []).append(c)
    out = []
    for (year, month), items in by_month.items():
        lead = items[0]["day"].weekday()  # blank cells before the first drawn day, so the weekday columns line up
        grid = "".join(f'<div class="cal-w">{w}</div>' for w in WEEKDAYS) + '<div></div>' * lead
        for c in items:
            price = f"€{c['cash']:.0f}" if c["cash"] is not None else "—"
            tag = {"seat": "seat", "best": "best day"}.get(c["state"], "")
            grid += (f'<div class="cal-c cal-{c["state"]}"><small>{c["day"].day}</small><b>{price}</b>' + (f"<i>{tag}</i>" if tag else "") + "</div>")
        out.append(f'<div class="cal"><div class="cal-m">{e(calendar.month_name[month])} {year}</div><div class="cal-g">{grid}</div></div>')
    out.append('<div class="cal-k"><span class="k-best">Award seat, cash is dear: best day for miles</span><span class="k-seat">Award seat</span>'
               '<span class="k-none">No award seat</span><span class="k-unk">Not checked</span></div>')
    return "".join(out)
