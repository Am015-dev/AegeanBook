"""Hotel price collector: Google Hotels via `stays` (free, reverse-engineered, may break).
Price = Google's lowest nightly rate across the sites it compares (Booking.com is usually one); not Booking-only."""
import math
import re
import statistics
import time
import unicodedata
from datetime import date, timedelta

import stays
from stays.search import client as _client

from par import parallel

# Google Hotels answers in the style of the asker's country, and the library sends none: from a server it got US-style prices before
# VAT and city tax (Amrâth Maastricht, 4-7 Dec, 2 adults: €129 a night, while from Greece Google shows €165 and Booking.com totals
# €504 for the 3 nights). Asking as a traveller in Greece gives the prices a Greek user sees, taxes included.
COUNTRY = "gr"
if "?" not in _client.Client.ENDPOINT:
    _client.Client.ENDPOINT += f"?hl=en&gl={COUNTRY}"
TAXED_SINCE = "2026-11-15T16:00:00"  # Google prices fetched before this were the US-style ones: never shown again


def plain(text):
    """A name as words to compare: no case, no accents, no punctuation, so "Amrâth Grand Hotel de l’Empereur" (Google's curly
    apostrophe) and "amrath grand hotel De L'Empereur" read the same."""
    text = "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", text.lower()).split())


def matches(name, name_filter):
    """True when every word of name_filter is in the hotel's name, as plain() reads them."""
    name = f" {plain(name)} "
    return all(w in name for w in plain(name_filter).split())


def pick(hotels, name_filter):
    """Cheapest-listed hotel whose name contains every word of name_filter (any case, accents or apostrophes), with a price."""
    ok = [h for h in hotels if h.get("display_price") and matches(h["name"], name_filter)]
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


def day_rows(places, query, nights, adults, fetched):
    """One row per hotel and check-in day with a price (the columns of data/hotel_nearby.csv): what each hotel around the one searched
    for costs a night, on each day, so the page can rank them for one check-in. Google's price is per night, averaged over the stay."""
    return [dict(query=query, nights=nights, adults=adults, day=d, hotel=name, price=price, fetched=fetched)
            for name, p in places.items() for d, price in sorted(p["prices"].items())]


def km(lat1, lng1, lat2, lng2):
    """Distance on the ground between two points, in km."""
    a = math.sin(math.radians(lat2 - lat1) / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * \
        math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(a))


def best_value(rating, total, reviews=None, min_rating=4.0, min_reviews=20):
    """Index of the best value among hotels: the highest guest rating per euro. Lists of equal length; a hotel needs `min_rating` and
    `min_reviews` (when known) to count, so a cheap unrated room never wins. None when no hotel qualifies."""
    reviews = reviews if reviews is not None else [None] * len(rating)
    ok = [(r / t, i) for i, (r, t, n) in enumerate(zip(rating, total, reviews))
          if r == r and t == t and t > 0 and r >= min_rating and (n is None or n != n or n >= min_reviews)]
    return max(ok)[1] if ok else None


SOLD_OUT = 2.5  # a night dearer than this many times the hotel's median: Google/Booking only had suites left (a fair, an event)
SMALL = 0.5  # a listing without a star class under this share of the area's middle price that night: likely a bed or a tiny room


def basis(adults):
    """What the page's main hotel prices are: Google Hotels' nightly rate for the whole room as a traveller in Greece sees it."""
    return f"a night for the room, {int(adults)} adult{'s' * (int(adults) > 1)}, taxes included"


def sold_out(prices):
    """Boolean Series, same index: the nights priced above SOLD_OUT times the hotel's median night. When a hotel is nearly full the
    searches return only its suites, so those nights say "busy" rather than what a room costs, and would wreck any average."""
    import pandas as pd  # here, not at the top: the daily runner imports this module without pandas installed
    p = pd.to_numeric(pd.Series(prices), errors="coerce")
    return p > SOLD_OUT * p.median()


HOSTEL = ("hostel", "dorm", "capsule", "pod hotel", "backpack")  # a hostel's lowest rate for 2 is usually 2 beds in a shared room


def small_room(stars, price, area, rating=None, reviews=None, names=None):
    """One bool per listing: no star class and priced under SMALL times the area's middle price that night (`area`, NaN when
    unknown). Such a price is often a bed in a shared room or a tiny room, so it should not headline "cheapest" or "best value".
    A listing many guests rate well (4.0+ from 50+ reviews, when given) is a real budget hotel, not flagged; a hostel always is
    (its name says so): its lowest rate for two is usually two beds in a shared room."""
    import pandas as pd  # here, not at the top: the daily runner imports this module without pandas installed
    n = len(stars)
    rating, reviews = rating if rating is not None else [None] * n, reviews if reviews is not None else [None] * n
    trusted = lambda r, v: pd.notna(r) and pd.notna(v) and r >= 4.0 and v >= 50
    names = names if names is not None else [""] * n
    hostel = lambda h: any(k in str(h).lower() for k in HOSTEL)
    return [hostel(h) or (bool(pd.isna(s) or s == "") and pd.notna(a) and pd.notna(p) and p < SMALL * a and not trusted(r, v))
            for s, p, a, r, v, h in zip(stars, price, area, rating, reviews, names)]


def checked_on(fetched):
    """When a set of prices was checked, from their ISO timestamps: "2 Nov", or "2–5 Nov" when they span days; "" when unknown."""
    import pandas as pd  # here, not at the top: the daily runner imports this module without pandas installed
    days = pd.to_datetime(pd.Series(fetched), errors="coerce").dropna().dt.normalize()
    if days.empty:
        return ""
    lo, hi = days.min(), days.max()
    return f"{lo:%-d %b}" if lo == hi else f"{lo:%-d %b}–{hi:%-d %b}" if lo.month != hi.month else f"{lo:%-d}–{hi:%-d %b}"


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
    assert matches("Amrâth Grand Hotel de l'Empereur", "Amrâth Grand Hotel de l’Empereur") and matches("Amrâth Grand Hotel", "amrath grand")
    assert not matches("Steigenberger Airport Hotel", "frankfurter hof")
    seen = {}
    note(seen, [{"name": "A", "lat": 50.11, "lng": 8.67, "display_price": 200, "star_class": 5},
                {"name": "B", "lat": 50.12, "lng": 8.68, "display_price": None}, {"name": "C", "display_price": 90}], date(2026, 11, 1))
    note(seen, [{"name": "A", "lat": 50.11, "lng": 8.67, "display_price": 100}], date(2026, 11, 2))
    rows = {r["hotel"]: r for r in place_rows(seen, "q", 1, 2, "t")}
    assert set(rows) == {"A", "B"} and (rows["A"]["low"], rows["A"]["typical"], rows["A"]["days"]) == (100, 150, 2)  # C has no place
    assert rows["B"]["low"] == "" and rows["B"]["days"] == 0
    assert [(r["hotel"], r["day"], r["price"]) for r in day_rows(seen, "q", 1, 2, "t")] == [("A", "2026-11-01", 200), ("A", "2026-11-02", 100)]
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
