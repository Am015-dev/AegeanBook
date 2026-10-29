"""Which days to name in the advice: a shortlist across the whole range, not one arbitrary day. Cash: the cheapest few days (several often tie or
sit within a few percent). Miles cost the same on every day that has a seat, so 'the lowest miles day' is just the first of the ties; what
matters is where a seat replaces the most cash. A new module on purpose: Streamlit Cloud can run a new page against older, already loaded
modules."""
import pandas as pd

N = 3  # days named in the advice


def _d(iso):
    return f"{pd.Timestamp(iso):%a %d %b}"


def _more(extra, tol):
    if extra <= 0:
        return ""
    return f" and {extra} more within {tol:.0%}" if tol else f" and {extra} more at this price"


def cheapest_days(cash, tol, n=N):
    """Up to n of the cheapest days as text: those within `tol` of the cheapest fare, cheapest first and the earliest first among equals, plus how
    many more qualify. cash: Series by iso day. '' when there is no fare."""
    s = cash.dropna().sort_index()
    if s.empty:
        return ""
    near = s[s <= s.min() * (1 + tol) + 1e-9].sort_values(kind="stable")
    return ", ".join(_d(d) for d in near.index[:n]) + _more(len(near) - min(n, len(near)), tol)


def best_miles_days(cash, miles, taxes=None, n=N):
    """The days where an award seat replaces the most cash (the cash fare minus the award taxes, over the miles), dearest first, each with its
    cash fare: 'best days to use them: Mon 23 Nov (cash €202), ...'. cash, miles, taxes: Series by iso day. '' when no day has both."""
    both = pd.concat([cash.rename("cash"), miles.rename("miles")], axis=1, join="inner").dropna().sort_index()
    if both.empty:
        return ""
    tax = taxes.reindex(both.index).fillna(0) if taxes is not None else 0
    top = ((both.cash - tax) / both.miles).sort_values(ascending=False, kind="stable").index[:n]
    return "best days to use them: " + ", ".join(f"{_d(d)} (cash €{both.cash[d]:.0f})" for d in top)


def cheapest_pairs(trips_cabin, tol, n=N):
    """The cheapest round trips as text ('Thu 26 Nov → Thu 03 Dec, ...'), those within `tol` of the cheapest total. trips_cabin: one cabin's rows
    of the round-trip table (depart, back, total), cheapest first."""
    if trips_cabin.empty:
        return ""
    near = trips_cabin[trips_cabin.total <= trips_cabin.total.min() * (1 + tol) + 1e-9]
    return ", ".join(f"{_d(r.depart)} → {_d(r.back)}" for r in near.head(n).itertuples()) + _more(len(near) - min(n, len(near)), tol)


def best_miles_pairs(out_miles, back_miles, nights, cash_of, out_taxes=None, back_taxes=None, n=N):
    """The round trips where award seats both ways replace the most cash: 'best trips to use them: Mon 23 Nov → Mon 30 Nov (cash €291), ...'.
    cash_of: {(depart iso, back iso): cash total}; the Series are miles (and taxes) by iso day. '' when no pair has seats both ways and a fare."""
    scored = []
    for d, m1 in sorted(out_miles.dropna().items()):
        for k in nights:
            back = (pd.Timestamp(d) + pd.Timedelta(days=int(k))).strftime("%Y-%m-%d")
            m2 = back_miles.get(back)
            if m2 is None or pd.isna(m2) or (d, back) not in cash_of:
                continue
            t1, t2 = (out_taxes.get(d) if out_taxes is not None else None), (back_taxes.get(back) if back_taxes is not None else None)
            tax = float(t1 + t2) if t1 is not None and t2 is not None and pd.notna(t1) and pd.notna(t2) else 0.0
            scored.append(((cash_of[(d, back)] - tax) / (m1 + m2), d, back))
    if not scored:
        return ""
    scored.sort(key=lambda s: -s[0])  # stable: equal values keep their date order
    return "best trips to use them: " + ", ".join(f"{_d(d)} → {_d(b)} (cash €{cash_of[(d, b)]:.0f})" for _, d, b in scored[:n])
