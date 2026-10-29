"""Cash-fare collector: Google Flights date grid via `fli` (free, reverse-engineered, may break).
One request returns the cheapest AEGEAN fare for every day in a range, the same grid Google Flights shows in its calendar."""
from datetime import date, timedelta

from fli.models import Airline, Airport, DateSearchFilters, FlightSegment, MaxStops, PassengerInfo, SeatType
from fli.search import SearchDates

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


def fetch_range(start, end, orig, dest, cabins=("economy", "business"), skip=frozenset(), nonstop=True, workers=2):
    """Yield (day, cabin, price|None, airlines, status) for every day not in `skip` (a set of (iso day, cabin)).
    status: ok, none (no AEGEAN flight that day) or error (Google didn't answer)."""
    days = [start + timedelta(i) for i in range((end - start).days + 1)]
    todo = [c for c in cabins if any((d.isoformat(), c) not in skip for d in days)]
    for cabin, grid, err in parallel(todo, lambda c: fetch_cabin(orig, dest, c, start, end, nonstop), workers):
        for d in days:
            if (d.isoformat(), cabin) in skip:
                continue
            p = None if err else grid.get(d.isoformat())
            yield d, cabin, p, "" if p is None else "Aegean", "error" if err else "ok" if p is not None else "none"


if __name__ == "__main__":  # self-check, no network
    d0 = date(2026, 11, 1)
    fetch_cabin = lambda o, d, cabin, s, e, n: {"2026-11-01": 100.0}
    got = list(fetch_range(d0, d0 + timedelta(1), "ATH", "FRA", cabins=("economy",)))
    assert [(g[0], g[2], g[4]) for g in got] == [(d0, 100.0, "ok"), (d0 + timedelta(1), None, "none")]
    got = list(fetch_range(d0, d0, "ATH", "FRA", skip={("2026-11-01", "economy")}))
    assert [g[1] for g in got] == ["business"]

    def boom(*a):
        raise RuntimeError("x")
    fetch_cabin = boom
    assert next(fetch_range(d0, d0, "ATH", "FRA", cabins=("economy",)))[4] == "error"
    assert valid_airport("ATH") and not valid_airport("XXX")
    print("ok")
