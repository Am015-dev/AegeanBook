"""Hotel price collector: Google Hotels via `stays` (free, reverse-engineered, may break).
Price = Google's lowest nightly rate across the sites it compares (Booking.com is usually one); not Booking-only."""
from datetime import date, timedelta

import stays

from par import parallel


def pick(hotels, name_filter):
    """Cheapest-listed hotel whose name contains every word of name_filter (case-insensitive), with a price."""
    words = name_filter.lower().split()
    ok = [h for h in hotels if h.get("display_price") and all(w in h["name"].lower() for w in words)]
    return min(ok, key=lambda h: h["display_price"], default=None)


def fetch_day(query, name_filter, day, nights=1, adults=2):
    r = stays.search_hotels(query, check_in=day.isoformat(), check_out=(day + timedelta(nights)).isoformat(),
                            adults=adults, currency="EUR")
    h = pick(r.get("hotels", []), name_filter)
    return None if h is None else (h["name"], h["display_price"])


def fetch_range(start, end, query, name_filter, nights=1, adults=2, skip=frozenset(), workers=3):
    """Yield (day, hotel, price|None, status) as lookups finish. status: ok, none (hotel not listed) or error. `skip` holds iso days."""
    todo = [start + timedelta(i) for i in range((end - start).days + 1) if (start + timedelta(i)).isoformat() not in skip]
    for d, r, err in parallel(todo, lambda d: fetch_day(query, name_filter, d, nights, adults), workers):
        yield d, *(r or ("", None)), "error" if err else "ok" if r else "none"


if __name__ == "__main__":  # self-check, no network
    hs = [{"name": "Steigenberger Frankfurter Hof", "display_price": 207},
          {"name": "Steigenberger Airport Hotel", "display_price": 150},
          {"name": "Steigenberger Frankfurter Hof", "display_price": None}]
    assert pick(hs, "frankfurter hof")["display_price"] == 207 and pick(hs, "steigenberger")["display_price"] == 150
    assert pick(hs, "hilton") is None
    d0 = date(2026, 11, 1)
    fetch_day = lambda *a: ("H", 1)
    assert [(g[0], g[3]) for g in fetch_range(d0, d0 + timedelta(1), "q", "f", skip={"2026-11-01"})] == [(d0 + timedelta(1), "ok")]
    fetch_day = lambda *a: None
    assert next(fetch_range(d0, d0, "q", "f"))[3] == "none"
    print("ok")
