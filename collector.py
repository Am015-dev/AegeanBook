"""Cash-fare collector: Google Flights date grid via `fli` (free, reverse-engineered, may break).
One request returns the cheapest AEGEAN fare for every day in a range, the same grid Google Flights shows in its calendar."""
import time
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
    """Yield (day, cabin, price|None, source, status, error) for every day not in `skip` ((iso day, cabin) pairs).
    status: ok, none (no AEGEAN flight, confirmed by two empty answers) or error (Google did not answer, after retries).
    The date grid is fast but has holes on quiet routes, so days it leaves out (or all days if it fails) are searched
    one by one. Google sometimes answers empty or errors for real flights, so empties are asked twice and failed lookups get
    one slower last pass."""
    days = [start + timedelta(i) for i in range((end - start).days + 1)]
    todo = [c for c in cabins if any((d.isoformat(), c) not in skip for d in days)]
    pending = []
    for cabin, grid, err in parallel(todo, lambda c: fetch_cabin(orig, dest, c, start, end, nonstop), 2):
        grid = {} if err else grid
        for d in days:
            if (d.isoformat(), cabin) in skip:
                continue
            if d.isoformat() in grid:
                yield d, cabin, grid[d.isoformat()], "calendar", "ok", None
            else:
                pending.append((d, cabin))

    def look(a):
        r = fetch_day(orig, dest, a[1], a[0], nonstop)
        if r is None:
            time.sleep(0.7)
            r = fetch_day(orig, dest, a[1], a[0], nonstop)
        return r

    def verdict(item, p, err):
        d, cabin = item
        return d, cabin, p, "" if p is None else "day search", "error" if err else "ok" if p is not None else "none", err

    failed, gave_up = [], None
    for item, p, err in parallel(pending, look, workers, give_up_after=6):
        if err and err.startswith("Gave up"):
            gave_up = err
        if err and not gave_up:
            failed.append(item)
        else:
            yield verdict(item, p, err)
    if gave_up:  # nothing works right now: report everything as failed instead of retrying slowly
        for item in failed:
            yield verdict(item, None, gave_up)
    elif failed:
        time.sleep(3)
        for item, p, err in parallel(failed, look, 1, retries=3):
            yield verdict(item, p, err)


if __name__ == "__main__":  # self-check, no network
    d0 = date(2026, 11, 1)
    d1, d2 = d0 + timedelta(1), d0 + timedelta(2)
    fetch_cabin = lambda o, d, cabin, s, e, n: {"2026-11-01": 100.0}
    fetch_day = lambda o, d, cabin, day, n: 222.0 if day == d1 else None
    got = list(fetch_range(d0, d2, "ATH", "FRA", cabins=("economy",)))
    assert sorted((g[0], g[2], g[3], g[4]) for g in got) == [
        (d0, 100.0, "calendar", "ok"), (d1, 222.0, "day search", "ok"), (d2, None, "", "none")]
    assert [g[1] for g in fetch_range(d0, d0, "ATH", "FRA", skip={("2026-11-01", "economy")})] == ["business"]

    calls = {"n": 0}

    def empty_once(o, d, cabin, day, n):  # Google answers empty the first time for a day that has a flight
        calls["n"] += 1
        return 150.0 if calls["n"] > 1 else None
    fetch_cabin, fetch_day = (lambda *a: {}), empty_once
    assert next(fetch_range(d0, d0, "ATH", "FRA", cabins=("economy",)))[2:5] == (150.0, "day search", "ok")

    def boom(*a):
        raise RuntimeError("x")
    time.sleep = lambda s: None  # keep the failure paths quick
    fetch_cabin = boom  # grid fails: every day is checked one by one
    fetch_day = lambda o, d, cabin, day, n: None
    assert [g[4] for g in fetch_range(d0, d0, "ATH", "FRA", cabins=("economy",))] == ["none"]
    fetch_day = boom
    last = next(fetch_range(d0, d0, "ATH", "FRA", cabins=("economy",)))
    assert last[4] == "error" and "RuntimeError" in last[5]
    assert valid_airport("ATH") and not valid_airport("XXX")
    print("ok")
