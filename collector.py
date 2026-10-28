"""Cash-fare collector: Google Flights via fast-flights (free, scraped, may break)."""
from datetime import date, timedelta

from fast_flights import FlightQuery, Passengers, create_query, get_flights

from par import parallel


def pick(flights, orig, dest, airline="aegean"):
    """Cheapest itinerary on exactly orig->dest operated by `airline`, or None."""
    ok = [f for f in flights
          if f.flights[0].from_airport.code == orig and f.flights[-1].to_airport.code == dest
          and any(airline in a.lower() for a in f.airlines)]
    return min(ok, key=lambda f: f.price, default=None)


def fetch_day(day, orig, dest, cabin):
    q = create_query(flights=[FlightQuery(date=day.isoformat(), from_airport=orig, to_airport=dest)],
                     trip="one-way", seat=cabin, passengers=Passengers(adults=1),
                     language="en-GB", currency="EUR")
    best = pick(get_flights(q), orig, dest)
    return None if best is None else (best.price, ", ".join(best.airlines))


def fetch_range(start, end, orig, dest, cabins=("economy", "business"), skip=frozenset(), workers=3):
    """Yield (day, cabin, price|None, airlines, status) as lookups finish.
    status: ok, none (no AEGEAN flight) or error (Google didn't answer). `skip` holds (iso day, cabin) pairs to leave alone."""
    days = [start + timedelta(i) for i in range((end - start).days + 1)]
    todo = [(d, c) for d in days for c in cabins if (d.isoformat(), c) not in skip]
    for (d, cabin), r, err in parallel(todo, lambda a: fetch_day(a[0], orig, dest, a[1]), workers):
        yield d, cabin, *(r or (None, "")), "error" if err else "ok" if r else "none"


if __name__ == "__main__":  # self-check, no network
    from types import SimpleNamespace as N
    ap = lambda c: N(code=c)
    mk = lambda p, a, o, d: N(price=p, airlines=a, flights=[N(from_airport=ap(o), to_airport=ap(d))])
    fl = [mk(408, ["Aegean"], "ATH", "FRA"), mk(300, ["Lufthansa"], "ATH", "FRA"),
          mk(200, ["Aegean"], "ATH", "SKG"), mk(380, ["Aegean", "Lufthansa"], "ATH", "FRA")]
    assert pick(fl, "ATH", "FRA").price == 380 and pick(fl, "ATH", "MUC") is None
    d0 = date(2026, 11, 1)
    fetch_day = lambda *a: (1, "Aegean")
    got = list(fetch_range(d0, d0, "ATH", "FRA", skip={("2026-11-01", "economy")}))
    assert [(g[1], g[4]) for g in got] == [("business", "ok")]
    fetch_day = lambda *a: None
    assert next(fetch_range(d0, d0, "ATH", "FRA", cabins=("economy",)))[4] == "none"
    def boom(*a): raise RuntimeError("x")
    fetch_day = boom
    assert next(fetch_range(d0, d0, "ATH", "FRA", cabins=("economy",)))[4] == "error"
    print("ok")
