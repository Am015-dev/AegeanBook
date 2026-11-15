"""Xotelo (free, keyless, unofficial, built on TripAdvisor data): a month cheap-day heatmap and Booking.com nightly rates.
Hotels are identified by a TripAdvisor key like g187337-d202275 (found in the hotel's TripAdvisor URL)."""
import json
import re
import urllib.parse
import urllib.request
from datetime import timedelta

API = "https://data.xotelo.com/api/"
TIERS = {"cheap_price_days": "cheap", "average_price_days": "average", "high_price_days": "high"}


def hotel_key(text):
    """Pull 'g123-d456' out of a TripAdvisor URL or a bare key; None if absent."""
    m = re.search(r"g\d+-d\d+", text or "")
    return m.group(0) if m else None


def _get(path, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = json.load(urllib.request.urlopen(f"{API}{path}?{q}", timeout=40))
    if r.get("error"):
        raise RuntimeError(r["error"].get("message", "Xotelo error"))
    return r["result"]


def parse_heatmap(result):
    return {d: tier for k, tier in TIERS.items() for d in result["heatmap"].get(k, [])}


def heatmap_range(key, start, end):
    """{iso day: cheap|average|high}. One call covers about the 30 days before its chk_out date."""
    out, chk_out = {}, end + timedelta(1)
    while True:
        out.update(parse_heatmap(_get("heatmap", hotel_key=key, chk_out=chk_out.isoformat())))
        chk_out -= timedelta(30)
        if chk_out <= start:
            return out


def booking_rate(key, day, nights=1, adults=2):
    """Booking.com's nightly rate in EUR for a stay starting `day` for `adults` people, or None if it isn't listed. Xotelo prices one
    adult unless told, so the number is always sent: a room for two, not a single, is what two travellers book."""
    r = _get("rates", hotel_key=key, chk_in=day.isoformat(), chk_out=(day + timedelta(nights)).isoformat(), currency="EUR", adults=adults)
    return next((x["rate"] for x in r["rates"] if x["code"] == "BookingCom"), None)


def location(key):
    """TripAdvisor's town code ('g188575') out of a hotel key or URL; None if absent."""
    m = re.search(r"g\d+(?=-d\d+)", key or "")
    return m.group(0) if m else None


def site_rates(result, nights):
    """Every site's offer in a Xotelo rates answer, cheapest first: [dict(site, code, night, total, tax)]. `night` is the site's
    nightly rate for the dates and adults asked, `total` that times the nights; sites without a rate are left out."""
    rows = [dict(site=r.get("name") or r["code"], code=r["code"], night=r["rate"], total=r["rate"] * nights, tax=r.get("tax"))
            for r in result.get("rates") or [] if r.get("rate")]
    return sorted(rows, key=lambda r: r["night"])


def sites_text(rows):
    """One hotel's site_rates in a few words: "Booking.com €143 / cheapest site €126 (Agoda.com)"."""
    if not rows:
        return ""
    b, low = next((r for r in rows if r["code"] == "BookingCom"), None), rows[0]
    if b is low:
        return f"Booking.com €{b['night']:,.0f} (the cheapest site)"
    return (f"Booking.com €{b['night']:,.0f} / " if b else "") + f"cheapest site €{low['night']:,.0f} ({low['site']})"


def town(location_key, get=None, most=300):
    """[(name, key)] of the hotels TripAdvisor lists in a town, from Xotelo's free list (100 a call, at most `most`)."""
    get, out = get or _get, []
    for offset in range(0, most, 100):
        r = get("list", location_key=location_key, limit=100, offset=offset)
        out += [(h["name"], h["key"]) for h in r.get("list") or [] if h.get("name") and h.get("key")]
        if offset + 100 >= (r.get("total_count") or 0):
            break
    return out


def key_for(name, listed, plain=None):
    """The key of the hotel called `name` among `listed` [(name, key)]: the same name as plain() reads it, else the one hotel whose
    name holds all the words of the other's. None when no hotel, or several, fit: a wrong hotel's price is worse than none."""
    import hotels
    plain = plain or hotels.plain
    same = [k for n, k in listed if plain(n) == plain(name)]
    near = same or [k for n, k in listed if _same(n, name, plain)]
    return near[0] if len(set(near)) == 1 else None


def site_url(code, name, key, day, nights, adults):
    """(url, what it shows) where the traveller sees that site's price: Booking.com, Agoda and Trip.com searched for the hotel with the
    dates and adults filled in; any other site through the TripAdvisor page Xotelo's numbers come from (dates set there)."""
    from booking import hotel_url
    out, q = day + timedelta(nights), urllib.parse.quote(name)
    who = f"{adults} adult{'s' * (adults > 1)}"
    if code == "BookingCom":
        return hotel_url(name, day, nights, adults), f"Booking.com, these dates, {who}"
    if code == "Agoda":
        return (f"https://www.agoda.com/search?textToSearch={q}&checkIn={day.isoformat()}&los={nights}&rooms=1&adults={adults}&children=0",
                f"Agoda, these dates, {who}")
    if code.startswith("Ctrip"):
        return (f"https://www.trip.com/hotels/list?keyword={q}&checkin={day.isoformat()}&checkout={out.isoformat()}&adult={adults}&crn=1",
                f"Trip.com, these dates, {who}")
    return f"https://www.tripadvisor.com/Hotel_Review-{key}-Reviews.html", "compare on TripAdvisor (set the dates there)"


RAPID = "https://xotelo-hotel-prices.p.rapidapi.com/api/search"  # Xotelo's name search: only through RapidAPI, with a key (free plan)


def _same(a, b, plain):
    return all(w in f" {plain(b)} " for w in plain(a).split()) or all(w in f" {plain(a)} " for w in plain(b).split())


def find_key(name, lat, lng, towns=(), rapid_key=None, get=None, search=None, plain=None, km=None):
    """The TripAdvisor key of a hotel from its name and place, without the traveller pasting a link. First in the free Xotelo list of
    each town in `towns` (TripAdvisor town codes like g187337, known from hotels saved nearby): the hotel with the same name, or one
    within 200 m sharing its first words. Then, with a RapidAPI key, Xotelo's name search, which works in any town. None if not found."""
    import hotels  # plain() and km(), passed in by the tests
    plain, km, get = plain or hotels.plain, km or hotels.km, get or _get
    close = lambda h: h.get("geo") and lat is not None and km(lat, lng, h["geo"]["latitude"], h["geo"]["longitude"]) < 0.2
    for town in dict.fromkeys(towns):
        for offset in range(0, 500, 100):
            r = get("list", location_key=town, limit=100, offset=offset)
            for h in r.get("list") or []:
                if _same(h["name"], name, plain) or close(h) and plain(h["name"]).split()[:1] == plain(name).split()[:1]:
                    return h["key"]
            if offset + 100 >= (r.get("total_count") or 0):
                break
    if rapid_key:
        found = (search or _rapid)(name, rapid_key)
        hit = next((h for h in found if _same(h["name"], name, plain)), None) or (found[0] if len(found) == 1 else None)
        return hit and hit["key"]
    return None


def _rapid(name, key):
    """[dict(name, key)] from Xotelo's name search on RapidAPI."""
    req = urllib.request.Request(RAPID + "?" + urllib.parse.urlencode(dict(query=name, location_type="accommodation")),
                                 headers={"x-rapidapi-key": key, "x-rapidapi-host": "xotelo-hotel-prices.p.rapidapi.com"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r).get("result") or {}
    out = []
    for h in res.get("list") or []:  # the key may sit under hotel_key or key, or only in the url: take whichever holds one
        k = hotel_key(" ".join(str(h.get(f) or "") for f in ("hotel_key", "key", "url")))
        if k and h.get("name"):
            out.append(dict(name=h["name"], key=k))
    return out


if __name__ == "__main__":  # self-check, no network
    from datetime import date
    assert hotel_key("https://www.tripadvisor.com/Hotel_Review-g187337-d202275-Reviews-X.html") == "g187337-d202275"
    assert hotel_key("g1-d2") == "g1-d2" and hotel_key("steigenberger") is None
    h = {"heatmap": {"cheap_price_days": ["2026-11-14"], "average_price_days": ["2026-11-13"], "high_price_days": ["2026-11-16"]}}
    assert parse_heatmap(h) == {"2026-11-14": "cheap", "2026-11-13": "average", "2026-11-16": "high"}
    calls = []
    _get = lambda path, **p: (calls.append(p["chk_out"]), {"heatmap": {}})[1]
    heatmap_range("k", date(2026, 11, 1), date(2026, 12, 31))  # 61 days -> 3 calls
    assert len(calls) == 3, calls
    town = {"total_count": 2, "list": [{"name": "Steigenberger Icon Frankfurter Hof", "key": "g187337-d202275",
                                        "geo": {"latitude": 50.1103, "longitude": 8.6761}},
                                       {"name": "Hotel Nearby", "key": "g187337-d1", "geo": {"latitude": 50.12, "longitude": 8.69}}]}
    lists = lambda path, **p: town
    assert find_key("Steigenberger Frankfurter Hof", 50.1103, 8.6760, ["g187337"], get=lists) == "g187337-d202275"  # same place, same first word
    assert find_key("Hotel Elsewhere", 48.0, 11.0, ["g187337"], get=lists) is None
    assert find_key("Hotel Elsewhere", 48.0, 11.0, [], rapid_key="k", search=lambda n, k: [{"name": "Hotel Elsewhere Munich", "key": "g1-d2"}]) == "g1-d2"
    print("ok")
