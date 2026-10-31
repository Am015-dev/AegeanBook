"""Fetch the flights of a day (number, times, Google's lowest fare) on GitHub's servers, run by .github/workflows/fetch-details.yml when
the Overview's "Show flight times" is pressed. Google rate-limits Streamlit Cloud's shared server, so the app asks GitHub. Writes
data/flight_details.csv, which the app reads from GitHub (flight_times.py).

    LEGS="ATH,IST,2026-12-08;IST,ATH,2026-12-14" CABIN=economy python fetch_details.py new_rows.csv
    python fetch_details.py --merge new_rows.csv   # add those rows to the current data/flight_details.csv (the workflow's save step)
"""
import csv
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

OUT = Path(__file__).parent / "data" / "flight_details.csv"
FIELDS = ["day", "orig", "dest", "cabin", "flight", "depart", "arrive", "minutes", "price", "fetched"]
KEY = ("day", "orig", "dest", "cabin", "flight")
CABINS = ("economy", "business")
KEEP = 30  # days a lookup is kept in the file (fetch_route.KEEP)
TOP = 6  # flights kept per day, cheapest first


def parse(legs, cabin):
    """[(orig, dest, day)] from "ATH,IST,2026-12-08;IST,ATH,2026-12-14"; airports are three letters, dates ISO, cabin economy or
    business."""
    if cabin not in CABINS:
        raise ValueError(f"not a cabin: {cabin}")
    out = []
    for leg in filter(None, legs.split(";")):
        o, d, a = [x.strip() for x in leg.split(",")]
        if not (len(o) == len(d) == 3 and o.isalpha() and d.isalpha()):
            raise ValueError(f"not an airport pair: {o} {d}")
        out.append((o.upper(), d.upper(), date.fromisoformat(a)))
    return out


def fetch_day(orig, dest, cabin, day):
    """The nonstop AEGEAN flights of one day from Google, cheapest first, as rows (without `fetched`)."""
    from fli.models import Airline, Airport, FlightSearchFilters, FlightSegment, MaxStops, PassengerInfo, SeatType
    from fli.search import SearchFlights
    f = FlightSearchFilters(passenger_info=PassengerInfo(adults=1), stops=MaxStops.NON_STOP, seat_type=SeatType[cabin.upper()],
                            airlines=[Airline.A3], flight_segments=[FlightSegment(departure_airport=[[Airport[orig], 0]],
                                                                                  arrival_airport=[[Airport[dest], 0]],
                                                                                  travel_date=day.isoformat())])
    r = SearchFlights().search(f, top_n=10, currency="EUR") or []
    rows = []
    ours = (x for x in r if x.price is not None and all(leg.airline == Airline.A3 for leg in x.legs))  # Google ignores the airline filter
    for x in sorted(ours, key=lambda x: x.price)[:TOP]:
        leg = x.legs[0]
        rows.append(dict(day=day.isoformat(), orig=orig, dest=dest, cabin=cabin, flight=f"{leg.airline.name.removeprefix('_')} {leg.flight_number}",
                         depart=leg.departure_datetime.strftime("%H:%M"), arrive=leg.arrival_datetime.strftime("%H:%M"),
                         minutes=x.duration, price=round(x.price)))
    return rows


def save(rows, path=None):
    """Upsert the rows into the file by (day, route, cabin, flight); rows fetched more than KEEP days ago and past days are dropped."""
    path, keep = path or OUT, []
    if path.exists():
        with path.open() as f:
            old = (datetime.now() - timedelta(days=KEEP)).isoformat()
            keep = [r for r in csv.DictReader(f) if r["fetched"] >= old and r["day"] > date.today().isoformat()]
    newest = {tuple(r[k] for k in KEY): r for r in keep + rows}
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(sorted(newest.values(), key=lambda r: (r["orig"], r["dest"], r["day"], r["cabin"], float(r["price"]))))


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main(legs, cabin, new, pause=30):
    """Fetch every (orig, dest, day); a day that failed (usually a Google rate limit) is retried once after a pause."""
    now = datetime.now().isoformat(timespec="seconds")
    rows, failed = [], 0
    for o, d, day in parse(legs, cabin):
        for attempt in (1, 2):
            try:
                found = fetch_day(o, d, cabin, day)
                rows += [dict(r, fetched=now) for r in found]
                print(f"{o}-{d} {day}: {len(found)} flights")
                break
            except Exception as err:
                print(f"{o}-{d} {day} attempt {attempt}: {err}")
                if attempt == 2:
                    failed += 1
                else:
                    time.sleep(pause)
    with open(new, "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} saved, {failed} days failed")
    return 0 if rows or not failed else 1


if __name__ == "__main__":
    if sys.argv[1] == "--merge":
        save(read(sys.argv[2]))
    else:
        sys.exit(main(os.environ["LEGS"], os.environ["CABIN"], sys.argv[1]))
