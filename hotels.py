"""Hotel price collector: Google Hotels via `stays` (free, reverse-engineered, may break).
Price = Google's lowest nightly rate across the sites it compares (Booking.com is usually one); not Booking-only."""
import math
import statistics
import time
from datetime import date, timedelta

import stays

from par import parallel


def pick(hotels, name_filter):
    """Cheapest-listed hotel whose name contains every word of name_filter (case-insensitive), with a price."""
    words = name_filter.lower().split()
    ok = [h for h in hotels if h.get("display_price") and all(w in h["name"].lower() for w in words)]
    return min(ok, key=lambda h: h["display_price"], default=None)


def note(places, hotels, day):
    """Every hotel of one search answer into places {name: dict(lat, lng, stars, rating, reviews, prices={iso day: price})}: the
    hotels around the one searched for, for the map."""
    for h in hotels:
        lat, lng = h.get("lat", h.get("latitude")), h.get("lng", h.get("longitude"))
        if lat is None or lng is None or not h.get("name"):
            continue
        p = places.setdefault(h["name"], dict(lat=lat, lng=lng, stars=h.get("star_class"), rating=h.get("overall_rating"),
                                              reviews=h.get("review_count"), prices={}))
        if h.get("display_price"):
            p["prices"][day.isoformat()] = h["display_price"]


def place_rows(places, query, nights, adults, fetched):
    """One row per hotel seen in the searches (the columns of data/hotel_places.csv): where it is and what a night cost."""
    out = []
    for name, p in places.items():
        days = sorted(p["prices"])
        prices = [p["prices"][d] for d in days]
        out.append(dict(query=query, nights=nights, adults=adults, hotel=name, lat=p["lat"], lng=p["lng"], stars=p["stars"] or "",
                        rating=p["rating"] or "", reviews=p["reviews"] or "", low=min(prices, default=""),
                        typical=round(statistics.median(prices)) if prices else "", days=len(days), first=days[0] if days else "",
                        last=days[-1] if days else "", fetched=fetched))
    return out


def km(lat1, lng1, lat2, lng2):
    """Distance on the ground between two points, in km."""
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def fetch_day(query, name_filter, day, nights=1, adults=2, places=None):
    r = stays.search_hotels(query, check_in=day.isoformat(), check_out=(day + timedelta(nights)).isoformat(),
                            adults=adults, currency="EUR")
    if not r.get("success", True):  # the library reports failures as an empty list plus an error: never read that as "no hotel"
        raise RuntimeError(r.get("error") or "Google Hotels did not answer")
    if places is not None:
        note(places, r.get("hotels", []), day)
    h = pick(r.get("hotels", []), name_filter)
    return None if h is None else (h["name"], h["display_price"])


def fetch_range(start, end, query, name_filter, nights=1, adults=2, skip=frozenset(), workers=3, places=None):
    """Yield (day, hotel, price|None, status, error). status: ok, none (no listing, confirmed by two empty answers) or error.
    Empties are asked twice and failed lookups get one slower last pass, because Google sometimes answers wrongly."""
    todo = [start + timedelta(i) for i in range((end - start).days + 1) if (start + timedelta(i)).isoformat() not in skip]

    more = {} if places is None else {"places": places}

    def look(d):
        r = fetch_day(query, name_filter, d, nights, adults, **more)
        if r is None:
            time.sleep(0.7)
            r = fetch_day(query, name_filter, d, nights, adults, **more)
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
    seen = {}
    note(seen, [{"name": "A", "lat": 50.11, "lng": 8.67, "display_price": 200, "star_class": 5},
                {"name": "B", "lat": 50.12, "lng": 8.68, "display_price": None}, {"name": "C", "display_price": 90}], date(2026, 11, 1))
    note(seen, [{"name": "A", "lat": 50.11, "lng": 8.67, "display_price": 100}], date(2026, 11, 2))
    rows = {r["hotel"]: r for r in place_rows(seen, "q", 1, 2, "t")}
    assert set(rows) == {"A", "B"} and (rows["A"]["low"], rows["A"]["typical"], rows["A"]["days"]) == (100, 150, 2)  # C has no place
    assert rows["B"]["low"] == "" and rows["B"]["days"] == 0
    assert 1.3 < km(50.11, 8.67, 50.12, 8.68) < 1.4
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
