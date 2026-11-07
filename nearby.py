"""Nearby airports (Skyscanner's "include nearby airports"): the same trip from or to an airport close to the chosen one, when its saved
fares make it cheaper. Pure functions over overview.legs(); the Overview shows them under the boarding pass.
A new module on purpose: Streamlit Cloud can run a page against older, already loaded modules."""
import math

import deals

VERSION = 1
KM = 200  # how far an airport may be, straight line, to count as nearby
MOST = 3


def km(places, a, b):
    """Great-circle km between airports a and b; places: DataFrame indexed by iata with lat, lng."""
    la1, lo1, la2, lo2 = map(math.radians, (places.lat[a], places.lng[a], places.lat[b], places.lng[b]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def cheaper(legs, places, orig, dest, start, end, cabin, nights, price, weekdays=None, oneway=False, flies=None, limit=KM):
    """Up to MOST trips that change one end of orig→dest for an airport within `limit` km and cost less than `price` (the pass's), most
    saved first: dicts with end ("from" or "to"), airport, km, price, depart, back, nights, saving. Only pairs with `cabin` fares saved in
    `legs` ({(orig, dest): {cabin: Series}}), the same window, nights and weekdays; `flies(o, d)` False drops a pair known not to be flown.
    oneway: the pass is a one-way (else only returns compare)."""
    if orig not in places.index or dest not in places.index:
        return []
    found = []
    for (o, d), f in legs.items():
        if cabin not in f or (o, d) == (orig, dest) or (d != dest and o != orig):
            continue
        end_, near, home = ("from", o, orig) if d == dest else ("to", d, dest)
        if near not in places.index or (flies and flies(o, d) is False):
            continue
        dist = km(places, home, near)
        if dist > limit:
            continue
        back = None if oneway else legs.get((d, o), {}).get(cabin)
        t = deals.best_trip(f[cabin], back, start, end, nights, weekdays)
        if t and t["price"] < price and (oneway or t["back"] is not None):  # a return only against a return
            found.append(dict(end=end_, airport=near, km=round(dist), saving=price - t["price"], **t))
    return sorted(found, key=lambda r: -r["saving"])[:MOST]
