"""The Aegean Pass inside the booking strategy. A pass is a block of one-way flights bought at a fixed price (here the 7-day pass, booked at
least 7 days ahead, and the 30-day pass, booked at least 30 days ahead); airport taxes and surcharges are paid on top, flight by flight. Each
pass is compared with the cash fares that could really be booked with it. The prices are inputs (they depend on the pass and change).
A new module on purpose: Streamlit Cloud can run a new page against older, already loaded modules, so this code is not added to one."""
import math

import pandas as pd

PASSES = (("7-day pass", 780.0, 7), ("30-day pass", 672.0, 30))  # (name, default price in EUR, minimum days before departure)
DEFAULT_FLIGHTS = 12  # assumed: the price per flight depends on how many flights the pass has, so it is always shown and editable
DEFAULT_TAXES = 30.0  # EUR per flight; what AEGEAN's award response showed for a nonstop Athens-Frankfurt flight


def effective_cost(price, flights, taxes):
    """Euros per flight: the pass price over its flights, plus the taxes paid on each."""
    return price / max(1, flights) + taxes


def cash_by_day(db, orig, dest, nonstop, start, end):
    """The cheapest saved economy fare by iso day (newest recording of each day); empty when there is none."""
    df = pd.read_sql("SELECT day, price FROM prices WHERE orig=? AND dest=? AND cabin='economy' AND nonstop=? AND price IS NOT NULL "
                     "AND day BETWEEN ? AND ? ORDER BY fetched", db, params=(orig, dest, int(nonstop), start.isoformat(), end.isoformat()))
    return df.drop_duplicates("day", keep="last").set_index("day").price.sort_index() if len(df) else pd.Series(dtype=float)


def assess(cash, price, flights, taxes, min_days, today):
    """How one pass compares with paying cash. cash: Series of the cheapest fare by iso day. Only days at least `min_days` ahead count, because
    the pass cannot be booked closer to departure. None when no such day has a fare."""
    ok = cash.dropna()
    ok = ok[[pd.Timestamp(d).date() >= today + pd.Timedelta(days=min_days) for d in ok.index]]
    if ok.empty:
        return None
    eff = effective_cost(price, flights, taxes)
    typical = float(ok.median())
    need = math.ceil(price / (typical - taxes)) if typical > taxes else None  # fewest flights that beat the typical fare
    cheaper = int((ok > eff).sum())
    return dict(effective=eff, days=len(ok), cheapest=float(ok.min()), typical=typical, pass_cheaper=cheaper, share=cheaper / len(ok),
                break_even=need if need is not None and need <= flights else None)


def verdict(a):
    """(css class, one plain sentence) from an assessment."""
    if a["effective"] <= a["cheapest"]:
        return "miles", "<b>Worth it on price.</b> Cheaper per flight than even the cheapest cash fare."
    if a["share"] >= 0.5:
        return "miles", "<b>Likely worth it.</b> Cheaper than cash on most days, and it adds flexibility."
    if a["share"] >= 0.2:
        return "cash", "<b>Only on the dear days.</b> Cash is cheaper most of the time."
    return "cash", "<b>Not worth it on price.</b> Cash is cheaper on most days."


def section_html(rows):
    """The pass block of the Economy strategy card. rows: [(name, price, flights, taxes, assessment or None, return)] where return is
    (cheapest cash round trip, round trips compared, round trips cheaper with the pass) or None."""
    row = lambda k, v: f'<div class="plan-r"><span>{k}</span><span>{v}</span></div>'
    out = ['<div class="plan-s">Aegean Pass (Economy Flex)</div>']
    for name, price, flights, taxes, a, ret in rows:
        title = f"{name}, €{price:,.0f} for {flights} flights"
        if a is None:
            out.append(row(title, "no bookable day with a fare yet"))
            continue
        out.append(row(title, f"<b>€{a['effective']:.0f}</b> per flight with taxes"))
        out.append(row("Beats cash on", f"<b>{a['pass_cheaper']} of {a['days']} days</b> · cash from €{a['cheapest']:.0f}, typical €{a['typical']:.0f}"))
        if ret:
            out.append(row("Round trip (2 flights)", f"<b>€{2 * a['effective']:.0f}</b> vs €{ret[0]:.0f} cash · cheaper on {ret[2]} of {ret[1]}"))
        cls, says = verdict(a)
        out.append(f'<p class="plan-v {cls}">{says}</p>')
    days = {name: d for name, _, d in PASSES}
    priced = [(a["effective"], name) for name, _, _, _, a, _ in rows if a]
    if len(priced) > 1:  # the question the two passes pose: which costs less per flight, and what that asks of you
        eff, best = min(priced)
        out.append(f'<p class="plan-v"><b>Cheapest per flight: {best}</b> (€{eff:.0f}), if you can book {days[best]} or more days ahead.</p>')
    out.append('<p class="plan-v" style="color:#6E6E73">Not priced: free changes, seat selection, a carry-on bag, Miles+Bonus at the Flex level.</p>')
    return "".join(out)


def with_extra(card, extra):
    """Put `extra` HTML inside a finished strategy card (a <div class="plan">...</div>) just before it closes."""
    return card[:-len("</div>")] + extra + "</div>" if card.endswith("</div>") else card + extra
