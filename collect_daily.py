"""Daily collector (run by .github/workflows/daily.yml), next 90 days: both cabins, nonstop, on four legs, plus one hotel
(Google Hotels and Booking.com). Writes data/daily.csv and data/daily_hotel.csv, which the app loads on start.
Retries failed days over several passes; exits 1 if any are still missing."""
import csv
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import history
import hotels
from collector import fetch_range
from par import parallel
from xotelo import booking_rate, heatmap_range

LEGS = [("ATH", "FRA"), ("FRA", "ATH"), ("ATH", "ZRH"), ("ZRH", "ATH")]
DAYS, PASSES, PAUSE = 90, 4, 120  # PAUSE seconds between passes lets a Google rate limit cool down
OUT = Path(__file__).parent / "data" / "daily.csv"
FIELDS = ["day", "orig", "dest", "cabin", "price", "source", "fetched", "nonstop"]
# the Hotels page's default search, so its results show there without pressing Update prices
HOTEL = dict(query="Steigenberger Frankfurt", filt="Frankfurter Hof", hkey="g187337-d202275", nights=1, adults=2)
HOTEL_OUT = Path(__file__).parent / "data" / "daily_hotel.csv"
HOTEL_FIELDS = ["source", "day", "hotel", "price", "tier", "fetched", "query", "filt", "hkey", "nights", "adults"]


def collect(fetch=fetch_range, today=None, pause=PAUSE):
    today = today or date.today()
    start, end = today + timedelta(1), today + timedelta(DAYS)
    want = {(d.isoformat(), o, t, c) for o, t in LEGS for c in ("economy", "business")
            for d in (start + timedelta(i) for i in range(DAYS))}
    got = {}
    for n in range(PASSES):
        before = len(got)
        for o, t in LEGS:
            skip = {(day, c) for day, o2, t2, c in got if (o2, t2) == (o, t)}
            if len(skip) == 2 * DAYS:
                continue
            for d, cabin, price, source, status, err in fetch(start, end, o, t, skip=skip, nonstop=True):
                if status != "error":
                    got[(d.isoformat(), o, t, cabin)] = (price, source)
                elif err:
                    print(f"  {o}-{t} {d} {cabin}: {err}")
        missing = want - got.keys()
        print(f"pass {n + 1}: {len(got)}/{len(want)} collected, {len(missing)} missing", flush=True)
        if not missing or (n and len(got) == before):  # a retry pass that got nothing new: Google is blocking, stop paying for waits
            break
        if n < PASSES - 1:
            time.sleep(pause)
    return got, want - got.keys()


def save(got, path=OUT):
    """Overwrite the file with today's results; days that failed today keep yesterday's row, so the app always has a price."""
    now = datetime.now().isoformat(timespec="seconds")
    rows = {}
    if path.exists():
        with path.open() as f:
            rows = {(r["day"], r["orig"], r["dest"], r["cabin"]): r for r in csv.DictReader(f)}
    fresh = []
    for (day, o, t, c), (price, source) in got.items():
        rows[(day, o, t, c)] = dict(day=day, orig=o, dest=t, cabin=c, price="" if price is None else price, source=source,
                                    fetched=now, nonstop=1)
        fresh.append(rows[(day, o, t, c)])
    if os.environ.get("DAILY_NEW_ROWS"):  # the workflow's save loop re-applies today's rows to the history of whatever main is newest
        with open(os.environ["DAILY_NEW_ROWS"], "w", newline="") as f:
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            w.writerows(fresh)
    try:  # the permanent history next to the file: never trimmed, one row per change
        history.append_changes(path.with_name("history_prices.csv"), fresh, ("day", "orig", "dest", "cabin", "nonstop"), ("price",))
    except Exception as e:  # the history is a bonus: never let it break the daily run
        print(f"  history: {type(e).__name__}: {e}")
    today = date.today().isoformat()
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(r for k, r in sorted(rows.items()) if k[0] > today)  # past days are dropped


def collect_hotel(google=hotels.fetch_range, rate=booking_rate, heat=heatmap_range, today=None, pause=PAUSE, places=None):
    """{(source, iso day): (hotel name, price, tier)} for Google Hotels, Booking.com and the cheap-day calendar, plus the
    number of check-in days still missing from Google or Booking.com."""
    h, today = HOTEL, today or date.today()
    start, end = today + timedelta(1), today + timedelta(DAYS)
    days = [start + timedelta(i) for i in range(DAYS)]
    got, missing = {}, 0
    for n in range(PASSES):
        if not any(src == "heat" for src, _ in got):
            try:
                got.update({("heat", d): (None, None, t) for d, t in heat(h["hkey"], start, end).items()
                            if start.isoformat() <= d <= end.isoformat()})
            except Exception as e:
                print(f"  heatmap: {type(e).__name__}: {e}")
        skip = {d for src, d in got if src == "google"}
        more = {} if places is None else dict(places=places)  # every hotel of each answer: the hotels around it, for the Hotels page
        for d, name, price, status, err in google(start, end, h["query"], h["filt"], h["nights"], h["adults"], skip=skip, **more):
            if status != "error":
                got[("google", d.isoformat())] = (name, price, None)
            elif err:
                print(f"  Google Hotels {d}: {err}")
        todo = [d for d in days if ("booking", d.isoformat()) not in got]
        for d, price, err in parallel(todo, lambda d: rate(h["hkey"], d, h["nights"], adults=h["adults"])):
            if err:
                print(f"  Booking.com {d}: {err}")
            else:
                got[("booking", d.isoformat())] = (None, price, None)
        missing = sum((src, d.isoformat()) not in got for src in ("google", "booking") for d in days)
        print(f"hotel pass {n + 1}: {2 * DAYS - missing}/{2 * DAYS} collected, {missing} missing", flush=True)
        if not missing:
            break
        if n < PASSES - 1:
            time.sleep(pause)
    return got, missing


def save_hotel(got, path=HOTEL_OUT):
    """Like save(): today's results, yesterday's row for anything that failed today, no past days."""
    now = datetime.now().isoformat(timespec="seconds")
    rows = {}
    if path.exists():
        with path.open() as f:
            rows = {(r["source"], r["day"]): r for r in csv.DictReader(f)}
    fresh = []
    for (src, day), (name, price, tier) in got.items():
        rows[(src, day)] = dict(source=src, day=day, hotel=name or "", price="" if price is None else price, tier=tier or "",
                                fetched=now, **HOTEL)
        fresh.append(rows[(src, day)])
    try:
        history.append_changes(path.with_name("history_hotel.csv"), fresh, ("source", "day", "query", "filt", "nights", "adults"),
                               ("price", "tier", "hotel"))
    except Exception as e:
        print(f"  history: {type(e).__name__}: {e}")
    today = date.today().isoformat()
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, HOTEL_FIELDS)
        w.writeheader()
        w.writerows(r for k, r in sorted(rows.items()) if k[1] > today)


if __name__ == "__main__":
    got, missing = collect()
    save(got)
    priced = sum(p is not None for p, _ in got.values())
    print(f"flights done: {priced} prices, {len(got) - priced} days with no flight, {len(missing)} missing")
    places = {}
    hgot, hmissing = collect_hotel(places=places)
    save_hotel(hgot)
    if around := os.environ.get("HOTEL_AROUND"):  # daily.yml merges these into hotel_places.csv / hotel_nearby.csv on the newest main
        import fetch_hotel
        stamp, h = datetime.now().isoformat(timespec="seconds"), HOTEL
        fetch_hotel.write(hotels.place_rows(places, h["query"], h["nights"], h["adults"], stamp), around + ".places", fetch_hotel.PLACE_FIELDS)
        fetch_hotel.write(hotels.day_rows(places, h["query"], h["nights"], h["adults"], stamp), around + ".nearby", fetch_hotel.NEARBY_FIELDS)
    print(f"hotel done: {sum(p is not None for _, p, _ in hgot.values())} prices, {hmissing} missing")
    sys.exit(1 if missing or hmissing else 0)
