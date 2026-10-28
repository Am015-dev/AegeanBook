"""Cash-fare collector: Google Flights via fast-flights (free, scraped, may break)."""
import time
from datetime import date, timedelta

from fast_flights import FlightQuery, Passengers, create_query, get_flights


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


def fetch_range(start, end, orig, dest, cabins=("economy", "business"), pause=2.0, skip=frozenset()):
    """Yield (day, cabin, price|None, airlines) politely, one request per day+cabin; `skip` holds (iso day, cabin) pairs to leave alone."""
    d = start
    while d <= end:
        for cabin in cabins:
            if (d.isoformat(), cabin) in skip:
                continue
            try:
                r = fetch_day(d, orig, dest, cabin)
            except Exception as e:  # ponytail: scraper breaks often; skip the day, keep going
                r = None
                print(f"{d} {cabin}: {e!r}")
            yield d, cabin, *(r or (None, ""))
            time.sleep(pause)
        d += timedelta(days=1)


if __name__ == "__main__":  # self-check, no network
    from types import SimpleNamespace as N
    ap = lambda c: N(code=c)
    mk = lambda p, a, o, d: N(price=p, airlines=a, flights=[N(from_airport=ap(o), to_airport=ap(d))])
    fl = [mk(408, ["Aegean"], "ATH", "FRA"), mk(300, ["Lufthansa"], "ATH", "FRA"),
          mk(200, ["Aegean"], "ATH", "SKG"), mk(380, ["Aegean", "Lufthansa"], "ATH", "FRA")]
    assert pick(fl, "ATH", "FRA").price == 380 and pick(fl, "ATH", "MUC") is None
    fetch_day = lambda *a: (1, "Aegean")
    d0 = date(2026, 11, 1)
    got = list(fetch_range(d0, d0, "ATH", "FRA", pause=0, skip={("2026-11-01", "economy")}))
    assert [g[1] for g in got] == ["business"]
    print("ok")
