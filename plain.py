"""Plain-language pieces shared by the Flights and Hotels pages: the one-sentence "Prices checked …" line that replaces the
"Last fetched" chips, and the Options filters with a one-line help each. A new module on purpose: Streamlit Cloud keeps older copies of
already loaded modules (ui.py among them) across redeploys, and a new module is always imported fresh."""
import pandas as pd
import streamlit as st

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _when(stamp):
    return f"{pd.Timestamp(stamp):%a %d %b %H:%M}"


def checked_sentence(items, what="Prices", unit="days", both_ways=False):
    """One sentence on when the prices shown were last checked. items: [(label, `fetched` stamps per day as a Series, days in range)].
    "Prices checked Sat 31 Oct 10:22 for all 30 days, both ways." when every part is complete and was checked at the same time;
    otherwise the parts are named: "Prices checked Sat 31 Oct 10:22 for ATH → FRA (all 30 days) and Fri 30 Oct 09:10 for FRA → ATH
    (12 of 30 days)." None when nothing was checked yet."""
    done = [(label, pd.Timestamp(f.max()), len(f), n) for label, f, n in items if len(f)]
    if not done:
        return None
    stamps = {t.strftime("%Y-%m-%d %H:%M") for _, t, _, _ in done}
    complete = all(k >= n for _, _, k, n in done) and len(done) == len(items)
    if complete and len(stamps) == 1:
        n = done[0][3]
        where = ", both ways" if both_ways and len(done) == 2 else (f" on {' and '.join(l for l, *_ in done)}" if len(done) > 1 else "")
        return f"{what} checked {_when(done[0][1])} for all {n} {unit}{where}."
    parts = []
    for label, t, k, n in done:
        cover = f"all {n} {unit}" if k >= n else f"{k} of {n} {unit}"
        parts.append(f"{_when(t)} for {label} ({cover})")
    missing = [label for label, f, _ in items if not len(f)]
    tail = f" {' and '.join(missing)} not yet." if missing else ""
    return f"{what} checked " + " and ".join(parts) + "." + tail


def options_filters(where, key, unit="day"):
    """The two fine filters under Options, each with a one-line help: how close to the cheapest still counts as cheap, and which weekdays
    count. Returns (tolerance as a fraction, weekdays). Keys `{key}-tol` and `{key}-days`, as before."""
    pct = where.pills("Cheap means within … of the cheapest", [0, 5, 10, 20, 30], default=10, required=True, key=f"{key}-tol",
                      format_func=lambda v: f"{v}%" if v else "Exact",
                      help=f"A {unit} counts as cheap when its price is at most this much above the cheapest one. Exact means the lowest price only.")
    days = where.pills("Days of the week", WEEKDAYS, selection_mode="multi", default=WEEKDAYS, key=f"{key}-days",
                       help=f"Only these weekdays count, for the cards, the cheapest {unit}s and the chart.")
    return pct / 100, days or WEEKDAYS


def nearly(n, tol):
    """' · one of 3 days within 10% of this price' / ' · one of 3 days at this price' / '' for a card's second line."""
    if n <= 1:
        return ""
    return f" · one of {n} days within {tol:.0%} of this price" if tol else f" · one of {n} days at this price"


if __name__ == "__main__":  # self-check, no network
    f = pd.Series(["2026-10-31T10:22:03"] * 30)
    assert checked_sentence([("ATH → FRA", f, 30), ("FRA → ATH", f, 30)], both_ways=True) == "Prices checked Sat 31 Oct 10:22 for all 30 days, both ways."
    assert checked_sentence([("Google Hotels", f, 30), ("Booking.com", f, 30)], unit="nights") == \
        "Prices checked Sat 31 Oct 10:22 for all 30 nights on Google Hotels and Booking.com."
    g = pd.Series(["2026-10-30T09:10:00"] * 12)
    assert checked_sentence([("ATH → FRA", f, 30), ("FRA → ATH", g, 30)], both_ways=True) == \
        "Prices checked Sat 31 Oct 10:22 for ATH → FRA (all 30 days) and Fri 30 Oct 09:10 for FRA → ATH (12 of 30 days)."
    assert checked_sentence([("ATH → FRA", f, 30), ("FRA → ATH", pd.Series([], dtype=object), 30)], both_ways=True) == \
        "Prices checked Sat 31 Oct 10:22 for ATH → FRA (all 30 days). FRA → ATH not yet."
    assert checked_sentence([("x", pd.Series([], dtype=object), 3)]) is None
    assert nearly(3, 0.1) == " · one of 3 days within 10% of this price" and nearly(2, 0) == " · one of 2 days at this price" and nearly(1, 0.1) == ""
    print("ok")
