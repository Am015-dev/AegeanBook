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


def breakdown(price, flights, taxes):
    """'€780 ÷ 12 flights = €65, plus €30 taxes = €95 for each flight': the pass price is spread over its flights, the taxes come on top."""
    each = price / max(1, flights)
    return f"€{price:,.0f} ÷ {flights} flights = €{each:,.0f}, plus €{taxes:,.0f} taxes = €{each + taxes:,.0f} for each flight"


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
