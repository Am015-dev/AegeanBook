"""One real lookup per data source, so a broken source is visible instead of showing up as odd or empty results."""
import time
from datetime import date, timedelta

import collector
import hotels
import xotelo

HOTEL_KEY = "g187337-d202275"  # Steigenberger Icon Frankfurter Hof


def _tests():
    d = date.today() + timedelta(60)
    return [
        ("Google Flights price calendar", lambda: collector.fetch_cabin("ATH", "FRA", "economy", d, d + timedelta(3)), lambda r: len(r) > 0),
        ("Google Flights day search", lambda: collector.fetch_day("ATH", "FRA", "economy", d), lambda r: r is not None),
        ("Google Hotels", lambda: hotels.fetch_day("Steigenberger Frankfurt", "Frankfurter Hof", d), lambda r: r is not None),
        ("Xotelo cheap-day calendar", lambda: xotelo.heatmap_range(HOTEL_KEY, d, d + timedelta(3)), lambda r: len(r) > 0),
        ("Xotelo Booking.com price", lambda: xotelo.booking_rate(HOTEL_KEY, d), lambda r: r is not None),
    ]


def run_checks(tests=None):
    """Return [(name, ok, seconds, detail)]. A source that answers but returns nothing is a failure too."""
    out = []
    for name, call, good in tests or _tests():
        t = time.time()
        try:
            r = call()
            ok, detail = bool(good(r)), (str(r)[:90] if good(r) else f"answered but returned nothing: {str(r)[:60]}")
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {str(e)[:120]}"
        out.append((name, ok, round(time.time() - t, 1), detail))
    return out


if __name__ == "__main__":  # self-check
    def boom():
        raise RuntimeError("down")
    res = run_checks([("a", lambda: [1], bool), ("b", boom, bool), ("c", lambda: None, lambda r: r is not None)])
    assert [r[1] for r in res] == [True, False, False] and "RuntimeError: down" in res[1][3] and "nothing" in res[2][3]
    print("ok")
