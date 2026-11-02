"""Fetch one hotel search on request, on GitHub's servers (run by .github/workflows/fetch-hotel.yml when the Hotels page's Update
prices is pressed), like fetch_route.py does for flights: Google rate-limits Streamlit Cloud's shared server, and what is fetched
there is lost when the app restarts. Writes data/hotel_searches.csv (the columns of data/daily_hotel.csv), which the app reads from
GitHub. The search comes in environment variables (QUERY, FILT, HKEY, NIGHTS, ADULTS, RANGES, BOOKING), never through a shell.

    python fetch_hotel.py new_rows.csv          # fetch, write this search's rows on their own too (and new_rows.csv.places)
    python fetch_hotel.py --merge new_rows.csv  # add those rows to the newest data/hotel_searches.csv (the workflow's save step)
"""
import csv
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import history
import hotels
import landmarks
from par import parallel
from xotelo import booking_rate, heatmap_range, hotel_key

OUT = Path(__file__).parent / "data" / "hotel_searches.csv"
PLACES = OUT.with_name("hotel_places.csv")  # every hotel the searches returned, with where it is, for the Hotels page's map
LANDMARKS = OUT.with_name("hotel_landmarks.csv")  # the town centre, main station and cathedral around each search, for the same map
PLACE_FIELDS = ["query", "nights", "adults", "hotel", "lat", "lng", "stars", "rating", "reviews", "low", "typical", "days", "first", "last",
                "fetched"]
FIELDS = ["source", "day", "hotel", "price", "tier", "fetched", "query", "filt", "hkey", "nights", "adults"]
KEEP = 30  # days a fetch is kept, as long as the app keeps it (db.RETENTION_DAYS)


def search(env):
    """The search from the environment, checked: dict(query, filt, hkey, nights, adults, ranges=[(first, last)], booking)."""
    query, filt = env.get("QUERY", "").strip()[:120], env.get("FILT", "").strip()[:120]
    if not query or not filt:
        raise ValueError("QUERY and FILT are needed")
    nights, adults = int(env.get("NIGHTS", "1")), int(env.get("ADULTS", "2"))
    if not (1 <= nights <= 14 and 1 <= adults <= 6):
        raise ValueError("nights 1-14, adults 1-6")
    ranges = []
    for r in filter(None, env.get("RANGES", "").split(";")):
        a, b = (date.fromisoformat(x.strip()) for x in r.split(","))
        if not (0 <= (b - a).days <= 400):
            raise ValueError(f"bad date range: {a} to {b}")
        ranges.append((a, b))
    if not ranges:
        raise ValueError("RANGES is needed")
    return dict(query=query, filt=filt, hkey=hotel_key(env.get("HKEY", "")) or "", nights=nights, adults=adults, ranges=ranges,
                booking=env.get("BOOKING", "1") not in ("0", "false", "False"))


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def write(rows, path, fields=FIELDS):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fields)
        w.writeheader()
        w.writerows(rows)


def save(rows, path=None):
    """Merge rows into the file: one row per source, day and search (the newest); rows fetched more than KEEP days ago and past days
    are dropped."""
    path = path or OUT
    old = (datetime.now() - timedelta(days=KEEP)).isoformat()
    keep = [r for r in read(path) if r["fetched"] >= old and r["day"] > date.today().isoformat()] if path.exists() else []
    key = lambda r: (r["source"], r["day"], r["query"], r["filt"], r["hkey"], str(r["nights"]), str(r["adults"]))
    newest = {key(r): r for r in keep + rows}
    write(sorted(newest.values(), key=lambda r: (r["query"], r["filt"], r["day"], r["source"])), path)
    # kept for good, like the daily hotel: every price a visitor fetched stays in the history the Database page shows and exports
    history.append_changes(Path(path).with_name("history_hotel.csv"), rows, ("source", "day", "query", "filt", "nights", "adults"),
                           ("price", "tier", "hotel"))


def save_places(rows, path=None):
    """Merge the hotels one search saw into the places file: the newest row per search and hotel, kept for good."""
    path = Path(path or PLACES)
    key = lambda r: (r["query"], str(r["nights"]), str(r["adults"]), r["hotel"])
    newest = {key(r): r for r in (read(path) if path.exists() else []) + rows}
    write(sorted(newest.values(), key=lambda r: (r["query"], r["hotel"])), path, PLACE_FIELDS)


def save_landmarks(rows, path=None):
    """Merge one search's landmarks into the file: a search's new set replaces its old one."""
    path = Path(path or LANDMARKS)
    queries = {r["query"] for r in rows}
    keep = [r for r in (read(path) if path.exists() else []) if r["query"] not in queries]
    write(sorted(keep + rows, key=lambda r: (r["query"], r["kind"])), path, landmarks.FIELDS)


def landmark_rows(s, places, path=None, find=landmarks.find):
    """The landmarks around the hotel searched for, unless the file has them already (a town's station does not move). [] when
    Nominatim fails: the map then shows the hotels alone."""
    path = Path(path or LANDMARKS)
    if path.exists() and any(r["query"] == s["query"] for r in read(path)):
        return []
    words = s["filt"].lower().split()
    spot = next((p for n, p in places.items() if all(w in n.lower() for w in words)), None) or next(iter(places.values()), None)
    if spot is None:
        return []
    try:
        return [dict(p, query=s["query"]) for p in find(spot["lat"], spot["lng"])]
    except Exception as e:
        print(f"  landmarks: {type(e).__name__}: {e}")
        return []


def fetch(s, passes=3, pause=90, google=None, rate=booking_rate, heat=heatmap_range, places=None):
    """Rows for the cheap-day calendar and Booking.com (with a TripAdvisor key) and Google Hotels on every day of the ranges.
    Google days that failed (usually a rate limit) are retried after a pause, like the daily run does."""
    google = google or hotels.fetch_range
    now = datetime.now().isoformat(timespec="seconds")
    row = lambda src, d, name=None, price=None, tier=None: dict(
        source=src, day=d if isinstance(d, str) else d.isoformat(), hotel=name or "", price="" if price is None else price,
        tier=tier or "", fetched=now, query=s["query"], filt=s["filt"], hkey=s["hkey"], nights=s["nights"], adults=s["adults"])
    rows = []
    for a, b in s["ranges"]:
        if s["hkey"]:
            try:
                tiers = {d: t for d, t in heat(s["hkey"], a, b).items() if a.isoformat() <= d <= b.isoformat()}
                rows += [row("heat", d, tier=t) for d, t in tiers.items()]
            except Exception as e:
                tiers = {}
                print(f"  heatmap: {type(e).__name__}: {e}")
            if s["booking"]:
                cheap = [date.fromisoformat(d) for d, t in sorted(tiers.items()) if t == "cheap"]
                for d, price, err in parallel(cheap, lambda d: rate(s["hkey"], d, s["nights"]), workers=3):
                    if not err:
                        rows.append(row("booking", d, price=price))
        done = set()
        for n in range(passes):
            failed = 0
            more = {} if places is None else {"places": places}
            for d, name, price, status, err in google(a, b, s["query"], s["filt"], s["nights"], s["adults"], skip=done, **more):
                if status == "error":
                    failed += 1
                    continue
                done.add(d.isoformat())
                rows.append(row("google", d, name, price))
            print(f"{s['filt']} {a}..{b} pass {n + 1}: {len(done)} saved, {failed} failed", flush=True)
            if not failed or n == passes - 1:
                break
            time.sleep(pause)  # lets a Google rate limit cool down
    return rows


if __name__ == "__main__":
    if sys.argv[1] == "--merge":
        save(read(sys.argv[2]))
        if os.path.exists(sys.argv[2] + ".places"):
            save_places(read(sys.argv[2] + ".places"))
        if os.path.exists(sys.argv[2] + ".landmarks") and (marks := read(sys.argv[2] + ".landmarks")):
            save_landmarks(marks)
    else:
        s, places = search(os.environ), {}
        got = fetch(s, places=places)
        write(got, sys.argv[1])
        write(hotels.place_rows(places, s["query"], s["nights"], s["adults"], datetime.now().isoformat(timespec="seconds")),
              sys.argv[1] + ".places", PLACE_FIELDS)
        marks = landmark_rows(s, places)
        write(marks, sys.argv[1] + ".landmarks", landmarks.FIELDS)
        print(f"{len(got)} rows, {len(places)} hotels seen, {len(marks)} landmarks")
