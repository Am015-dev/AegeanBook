"""Mixing airlines on a return (Kayak calls it a Hacker Fare): AEGEAN one way and Sky Express the other, when that beats both airlines'
own cheapest return. Pure functions over cash_by_day-style series (iso day -> euros). A new module on purpose: Streamlit Cloud can run a
page against older, already loaded modules."""
from datetime import date, timedelta

import pandas as pd

A3, GQ = "AEGEAN", "Sky Express"


def _clean(s):
    return pd.to_numeric(s, errors="coerce").dropna() if s is not None else pd.Series(dtype=float)


def _cheapest(out, back, nights, weekdays=None):
    """(total, depart, back, out price, back price) of the cheapest out + back pair `nights` apart, or None."""
    o, b, best = _clean(out), _clean(back), None
    for d, p in o.items():
        day = date.fromisoformat(str(d)[:10])
        if weekdays is not None and f"{day:%a}" not in weekdays:
            continue
        for n in nights:
            r = (day + timedelta(n)).isoformat()
            if r in b.index and (best is None or p + b[r] < best[0]):
                best = (float(p + b[r]), day, day + timedelta(n), float(p), float(b[r]))
    return best  # ponytail: days x nights scan, a few thousand pairs at most


def cheapest_mix(a3_out, a3_back, gq_out, gq_back, nights, weekdays=None):
    """The cheapest return flying one airline out and the other back, when it costs less than both single-airline returns:
    dict(out_airline, back_airline, depart, back, out_price, back_price, total, saving), else None (also None when neither airline
    has a return of its own to beat). `nights` is the stay lengths allowed, `weekdays` (Mon..Sun) the days to leave on."""
    singles = [t for t in (_cheapest(a3_out, a3_back, nights, weekdays), _cheapest(gq_out, gq_back, nights, weekdays)) if t]
    mixes = [(t, oa, ba) for t, oa, ba in ((_cheapest(a3_out, gq_back, nights, weekdays), A3, GQ),
                                           (_cheapest(gq_out, a3_back, nights, weekdays), GQ, A3)) if t]
    if not singles or not mixes:
        return None
    (total, dep, ret, op, bp), oa, ba = min(mixes, key=lambda m: m[0][0])
    single = min(t[0] for t in singles)
    if total >= single:
        return None
    return dict(out_airline=oa, back_airline=ba, depart=dep, back=ret, out_price=op, back_price=bp, total=total, saving=single - total)


def sentence(m, pax=1):
    """The mix in one plain line, euros for `pax` travellers."""
    return (f"Mix airlines: out on {m['out_airline']} {m['depart']:%a %-d %b} (€{m['out_price'] * pax:,.0f}), "
            f"back on {m['back_airline']} {m['back']:%a %-d %b} (€{m['back_price'] * pax:,.0f}) = €{m['total'] * pax:,.0f}, "
            f"€{m['saving'] * pax:,.0f} less than the cheapest single-airline return.")


NOTE = ("Two separate tickets: if the first flight is late or cancelled, the second airline does not have to help you. "
        "Bags are booked separately with each airline.")
