"""Cash-fare collector: Google Flights date grid via `fli` (free, reverse-engineered, may break).
One request returns the cheapest AEGEAN fare for every day in a range, the same grid Google Flights shows in its calendar."""
from datetime import date, timedelta

from fli.models import (Airline, Airport, DateSearchFilters, FlightSearchFilters, FlightSegment, MaxStops, PassengerInfo,
                        SeatType)
from fli.search import SearchDates, SearchFlights

from par import parallel

SEATS = {"economy": SeatType.ECONOMY, "business": SeatType.BUSINESS}


def valid_airport(code):
    return code in Airport.__members__


def fetch_cabin(orig, dest, cabin, start, end, nonstop=True):
    """{iso day: cheapest AEGEAN price in EUR} for one cabin; days with no flight are absent."""
    f = DateSearchFilters(passenger_info=PassengerInfo(adults=1), stops=MaxStops.NON_STOP if nonstop else MaxStops.ANY,
                          flight_segments=[FlightSegment(departure_airport=[[Airport[orig], 0]],
                                                         arrival_airport=[[Airport[dest], 0]], travel_date=start.isoformat())],
                          seat_type=SEATS[cabin], airlines=[Airline.A3], from_date=start.isoformat(), to_date=end.isoformat())
    r = SearchDates().search(f, currency="EUR")
    if r is None:
        raise RuntimeError("Google returned no date grid")
    return {p.date[0].date().isoformat(): p.price for p in r}


def fetch_day(orig, dest, cabin, day, nonstop=True):
    """Cheapest AEGEAN price on one day from a real flight search, or None. Slower than the grid but complete."""
    f = FlightSearchFilters(passenger_info=PassengerInfo(adults=1), stops=MaxStops.NON_STOP if nonstop else MaxStops.ANY,
                            seat_type=SEATS[cabin], airlines=[Airline.A3],
                            flight_segments=[FlightSegment(departure_airport=[[Airport[orig], 0]],
                                                           arrival_airport=[[Airport[dest], 0]], travel_date=day.isoformat())])
    r = SearchFlights().search(f, top_n=10, currency="EUR")
    return min((x.price for x in r), default=None) if r else None


def fetch_range(start, end, orig, dest, cabins=("economy", "business"), skip=frozenset(), nonstop=True, workers=3):
    """Yield (day, cabin, price|None, airlines, status) for every day not in `skip` (a set of (iso day, cabin)).
    status: ok, none (no AEGEAN flight that day) or error (Google didn't answer).
    The date grid answers fast but has holes on quiet routes (it skipped a real nonstop on ATH-BIO), so days it
    leaves out, or all days if it fails, are checked one by one."""
    days = [start + timedelta(i) for i in range((end - start).days + 1)]
    todo = [c for c in cabins if any((d.isoformat(), c) not in skip for d in days)]
    missing = []
    for cabin, grid, err in parallel(todo, lambda c: fetch_cabin(orig, dest, c, start, end, nonstop), 2):
        grid = {} if err else grid
        for d in days:
            if (d.isoformat(), cabin) in skip:
                continue
            if d.isoformat() in grid:
                yield d, cabin, grid[d.isoformat()], "Aegean", "ok"
            else:
                missing.append((d, cabin))
    for (d, cabin), p, err in parallel(missing, lambda a: fetch_day(orig, dest, a[1], a[0], nonstop), workers):
        yield d, cabin, p, "" if p is None else "Aegean", "error" if err else "ok" if p is not None else "none"


if __name__ == "__main__":  # self-check, no network
    d0 = date(2026, 11, 1)
    fetch_cabin = lambda o, d, cabin, s, e, n: {"2026-11-01": 100.0}
    fetch_day = lambda o, d, cabin, day, n: 222.0 if day == d0 + timedelta(1) else None
    got = list(fetch_range(d0, d0 + timedelta(2), "ATH", "FRA", cabins=("economy",)))
    assert sorted((g[0], g[2], g[4]) for g in got) == [(d0, 100.0, "ok"), (d0 + timedelta(1), 222.0, "ok"), (d0 + timedelta(2), None, "none")]
    got = list(fetch_range(d0, d0, "ATH", "FRA", skip={("2026-11-01", "economy")}))
    assert [g[1] for g in got] == ["business"]

    def boom(*a):
        raise RuntimeError("x")
    fetch_cabin = boom  # grid fails: every day is checked one by one
    assert [g[4] for g in fetch_range(d0, d0, "ATH", "FRA", cabins=("economy",))] == ["none"]
    fetch_day = boom
    assert next(fetch_range(d0, d0, "ATH", "FRA", cabins=("economy",)))[4] == "error"
    assert valid_airport("ATH") and not valid_airport("XXX")
    print("ok")
