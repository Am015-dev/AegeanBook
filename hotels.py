"""Hotel price collector: Google Hotels via `stays` (free, reverse-engineered, may break).
Price = Google's lowest nightly rate across the sites it compares (Booking.com is usually one); not Booking-only."""
import time
from datetime import timedelta

import stays


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


def fetch_range(start, end, query, name_filter, nights=1, adults=2, pause=2.0):
    d = start
    while d <= end:
        try:
            r = fetch_day(query, name_filter, d, nights, adults)
        except Exception as e:  # ponytail: scraper breaks often; skip the day, keep going
            r = None
            print(f"{d}: {e!r}")
        yield d, *(r or ("", None))
        time.sleep(pause)
        d += timedelta(days=1)


if __name__ == "__main__":  # self-check, no network
    hs = [{"name": "Steigenberger Frankfurter Hof", "display_price": 207},
          {"name": "Steigenberger Airport Hotel", "display_price": 150},
          {"name": "Steigenberger Frankfurter Hof", "display_price": None}]
    assert pick(hs, "frankfurter hof")["display_price"] == 207 and pick(hs, "steigenberger")["display_price"] == 150
    assert pick(hs, "hilton") is None
    print("ok")
