"""Hotel price collector: Google Hotels via `stays` (free, reverse-engineered, may break).
Price = Google's lowest nightly rate across the sites it compares (Booking.com is usually one); not Booking-only."""
import time
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
    if not r.get("success", True):  # the library reports failures as an empty list plus an error: never read that as "no hotel"
        raise RuntimeError(r.get("error") or "Google Hotels did not answer")
    h = pick(r.get("hotels", []), name_filter)
    return None if h is None else (h["name"], h["display_price"])


def fetch_range(start, end, query, name_filter, nights=1, adults=2, skip=frozenset(), workers=3):
    """Yield (day, hotel, price|None, status, error). status: ok, none (no listing, confirmed by two empty answers) or error.
    Empties are asked twice and failed lookups get one slower last pass, because Google sometimes answers wrongly."""
    todo = [start + timedelta(i) for i in range((end - start).days + 1) if (start + timedelta(i)).isoformat() not in skip]

    def look(d):
        r = fetch_day(query, name_filter, d, nights, adults)
        if r is None:
            time.sleep(0.7)
            r = fetch_day(query, name_filter, d, nights, adults)
        return r

    def verdict(d, r, err):
        return d, *(r or ("", None)), "error" if err else "ok" if r else "none", err

    failed, gave_up = [], None
    for d, r, err in parallel(todo, look, workers, give_up_after=6):
        if err and err.startswith("Gave up"):
            gave_up = err
        if err and not gave_up:
            failed.append(d)
        else:
            yield verdict(d, r, err)
    if gave_up:  # nothing works right now: report everything as failed instead of retrying slowly
        for d in failed:
            yield verdict(d, None, gave_up)
    elif failed:
        time.sleep(3)
        for d, r, err in parallel(failed, look, 1, retries=3):
            yield verdict(d, r, err)


if __name__ == "__main__":  # self-check, no network
    hs = [{"name": "Steigenberger Frankfurter Hof", "display_price": 207},
          {"name": "Steigenberger Airport Hotel", "display_price": 150},
          {"name": "Steigenberger Frankfurter Hof", "display_price": None}]
    assert pick(hs, "frankfurter hof")["display_price"] == 207 and pick(hs, "steigenberger")["display_price"] == 150
    assert pick(hs, "hilton") is None
    d0 = date(2026, 11, 1)
    fetch_day = lambda *a: ("H", 1)
    assert [(g[0], g[3]) for g in fetch_range(d0, d0 + timedelta(1), "q", "f", skip={"2026-11-01"})] == [(d0 + timedelta(1), "ok")]
    time.sleep = lambda s: None
    fetch_day = lambda *a: None
    assert next(fetch_range(d0, d0, "q", "f"))[3] == "none"

    def boom(*a):
        raise RuntimeError("x")
    fetch_day = boom
    last = next(fetch_range(d0, d0, "q", "f"))
    assert last[3] == "error" and "RuntimeError" in last[4]
    print("ok")
