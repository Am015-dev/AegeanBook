"""Cash-fare collector: Google Flights date grid via `fli` (free, reverse-engineered, may break).
One request returns the cheapest AEGEAN fare for every day in a range, the same grid Google Flights shows in its calendar."""
import time
from datetime import date, timedelta

from fli.models import (Airline, Airport, DateSearchFilters, FlightSearchFilters, FlightSegment, MaxStops, PassengerInfo,
                        SeatType)
from fli.search import SearchDates, SearchFlights

from par import RATE_LIMITED, parallel

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
    one slower last pass. When Google has no grid for the route at all, three spread-out days are probed first: all empty means
    AEGEAN does not fly it, and every day is reported none (source "probe") without the day-by-day search."""
    days = [start + timedelta(i) for i in range((end - start).days + 1)]
    todo = [c for c in cabins if any((d.isoformat(), c) not in skip for d in days)]
    pending, limited, grid_ok, no_grid = [], False, set(), set()
    for cabin, grid, err in parallel(todo, lambda c: fetch_cabin(orig, dest, c, start, end, nonstop), 2):
        if err and "429" in err:  # rate-limited: do not hammer Google with 60 more lookups
            for d in days:
                if (d.isoformat(), cabin) not in skip:
                    yield d, cabin, None, "", "error", RATE_LIMITED
            limited = True
            continue
        if not err:
            grid_ok.add(cabin)
        elif "no date grid" in err:  # Google has no calendar for the route at all: usually AEGEAN does not fly it (ATH-LCY)
            no_grid.add(cabin)
        grid = {} if err else grid
        for d in days:
            if (d.isoformat(), cabin) in skip:
                continue
            if d.isoformat() in grid:
                yield d, cabin, grid[d.isoformat()], "calendar", "ok", None
            else:
                pending.append((d, cabin))

    if limited:
        return
    # Each request counts towards Google's limit (HTTP 429 on the app's shared server). Every day the calendar left out is still searched:
    # its gaps include real flights (ATH-BIO summer Sundays, absent from the calendar), so no weekday or date is ever assumed empty.
    def look(a):
        r = fetch_day(orig, dest, a[1], a[0], nonstop)
        if r is None and a[1] not in grid_ok:  # the calendar already said "empty" once for its cabins; otherwise ask a second time
            time.sleep(0.7)
            r = fetch_day(orig, dest, a[1], a[0], nonstop)
        return r

    def verdict(item, p, err):
        d, cabin = item
        return d, cabin, p, "" if p is None else "day search", "error" if err else "ok" if p is not None else "none", err

    no_flight, failed, gave_up = set(), [], None
    order = sorted({c for _, c in pending}, key=lambda c: c != "economy")  # economy first: a day without one has no flight at all
    for i, cabin in enumerate(order):
        batch = [a for a in pending if a[1] == cabin]
        if cabin != "economy":
            for a in batch:
                if a[0] in no_flight:
                    yield a[0], cabin, None, "", "none", None
            batch = [a for a in batch if a[0] not in no_flight]
        if cabin in no_grid and len(batch) > 3:  # no calendar: 3 well-spread days settle "no such route" instead of 90 lookups, twice
            probes = {}  # asked once each: three empty days spread over the range confirm each other
            for item, p, err in parallel([batch[0], batch[len(batch) // 2], batch[-1]], lambda a: fetch_day(orig, dest, a[1], a[0], nonstop), workers):
                probes[item] = (p, err)
                gave_up = RATE_LIMITED if err and "429" in err else gave_up
            if gave_up:
                for item in failed + batch + [a for a in pending if a[1] in order[i + 1:]]:
                    yield verdict(item, None, gave_up)
                return
            if all(p is None and not err for p, err in probes.values()):
                for item in batch:
                    no_flight.add(item[0])
                    yield item[0], cabin, None, "probe", "none", None
                continue
            for item, (p, err) in probes.items():  # a flight after all (or Google failed): keep what the probes found, search the rest as usual
                if not err:
                    batch.remove(item)
                    if p is None:
                        no_flight.add(item[0])
                    yield verdict(item, p, None)
        for item, p, err in parallel(batch, look, workers, give_up_after=6):
            if err and ("429" in err or err.startswith("Gave up")):
                gave_up = RATE_LIMITED if "429" in err else err
            if err:
                failed.append(item)
                continue
            if p is None:
                no_flight.add(item[0])
            yield verdict(item, p, None)
        if gave_up:  # Google is refusing or unreachable: report what is left as failed instead of asking more
            for item in failed + [a for a in pending if a[1] in order[i + 1:]]:
                yield verdict(item, None, gave_up)
            return
    if failed:
        time.sleep(3)
        for item, p, err in parallel(failed, look, 1, retries=3):
            yield verdict(item, p, err)


def gaps(start, end, skip, cabins=("economy", "business")):
    """[(first, last)] runs of consecutive days from `start` to `end` that still miss a cabin in `skip` ((iso day, cabin) pairs already
    saved), so a fetch asks only for what is missing: May to July saved and August asked for gives just August."""
    out = []
    for i in range((end - start).days + 1):
        d = start + timedelta(i)
        if all((d.isoformat(), c) in skip for c in cabins):
            continue
        if out and out[-1][1] == d - timedelta(1):
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out
