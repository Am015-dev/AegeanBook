"""Shared SQLite store. Rows older than RETENTION_DAYS are deleted on every connect; fresh rows are reused instead of re-fetched."""
import csv
import os
import sqlite3
from datetime import datetime, timedelta

import gh

RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 30))  # fetched days are kept and reused this long; "Re-check" fetches again

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices(day TEXT, orig TEXT, dest TEXT, cabin TEXT, price REAL, airlines TEXT, fetched TEXT, nonstop INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS awards(day TEXT, orig TEXT, dest TEXT, miles INT, taxes REAL, PRIMARY KEY(day, orig, dest));
CREATE TABLE IF NOT EXISTS award_miles(day TEXT, orig TEXT, dest TEXT, cabin TEXT, miles INT, nonstop INT, fetched TEXT, taxes REAL);
CREATE TABLE IF NOT EXISTS heat(hkey TEXT, day TEXT, tier TEXT, fetched TEXT);
CREATE TABLE IF NOT EXISTS booking(hkey TEXT, day TEXT, nights INT, price REAL, fetched TEXT);
CREATE TABLE IF NOT EXISTS runs(page TEXT, label TEXT, started TEXT, seconds REAL, total INT, ok INT, empty INT, failed INT, error TEXT);
CREATE TABLE IF NOT EXISTS hotel(day TEXT, query TEXT, filt TEXT, nights INT, hotel TEXT, price REAL, fetched TEXT, adults INT DEFAULT 2);
"""


def cutoff():
    return (datetime.now() - timedelta(days=RETENTION_DAYS)).isoformat()


def saved_flight_days(db, orig, dest, nonstop, start, end):
    """(iso day, cabin) pairs already saved for this route and date range; the next update skips them."""
    return set(db.execute("SELECT day, cabin FROM prices WHERE orig=? AND dest=? AND nonstop=? AND fetched>=? AND day BETWEEN ? AND ?",
                          (orig, dest, int(nonstop), cutoff(), start.isoformat(), end.isoformat())))


def saved_hotel_days(db, query, filt, nights, adults, start, end):
    """Iso days already saved for this hotel search and date range; the next update skips them."""
    return {r[0] for r in db.execute("SELECT day FROM hotel WHERE query=? AND filt=? AND nights=? AND adults=? AND fetched>=? "
                                     "AND day BETWEEN ? AND ?",
                                     (query, filt, int(nights), int(adults), cutoff(), start.isoformat(), end.isoformat()))}


def _number(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if x != x else x  # NaN -> None


def save_awards(db, rows, orig, dest):
    """rows: (iso day, miles, taxes) for every day shown in the editor. A day without positive miles is deleted, so clearing a
    cell really clears it; taxes default to 0."""
    for day, miles, taxes in rows:
        miles, taxes = _number(miles), _number(taxes)
        if miles and miles > 0:
            db.execute("INSERT OR REPLACE INTO awards VALUES(?,?,?,?,?)", (day, orig, dest, int(miles), max(taxes or 0, 0)))
        else:
            db.execute("DELETE FROM awards WHERE day=? AND orig=? AND dest=?", (day, orig, dest))
    db.commit()


def log_run(db, page, label, started, seconds, j):
    """One row per price update, for the Stats page."""
    db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)", (page, label, started, round(seconds, 1), j["total"], j["ok"],
                                                             j["empty"], j["failed"], j["error"] or (j["errors"] or [None])[0]))
    db.commit()


DAILY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "daily.csv")


ROUTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "routes.csv")  # fetched on GitHub on request (fetch_route.py)


def load_daily(db, path=None, label="Daily"):
    """Load the prices the daily GitHub job saved (collect_daily.py), or the routes fetched on GitHub (fetch_route.py, label "Fetched"),
    once per file version."""
    path = path or DAILY
    if not os.path.exists(path):
        return
    with open(path) as f:
        rows = list(csv.DictReader(f))
    for fetched in {r["fetched"] for r in rows} - {r[0] for r in db.execute("SELECT DISTINCT fetched FROM prices")}:
        db.executemany("INSERT INTO prices VALUES(?,?,?,?,?,?,?,?)",
                       [(r["day"], r["orig"], r["dest"], r["cabin"], float(r["price"]) if r["price"] else None, r["source"],
                         r["fetched"], int(r["nonstop"])) for r in rows if r["fetched"] == fetched])
        got = [r for r in rows if r["fetched"] == fetched]
        priced = sum(bool(r["price"]) for r in got)
        db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)", ("daily", f"{label}: {len({(r['orig'], r['dest']) for r in got})} routes",
                                                                 fetched, None, len(got), priced, len(got) - priced, 0, None))


DAILY_AWARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "daily_award.csv")


def load_daily_award(db, path=None):
    """Load the AEGEAN award miles collected on the owner's computer (award_collector.py fetch), once per file version.
    A day with no award seats has an empty miles cell: it was checked, there was nothing."""
    path = path or DAILY_AWARD
    if not os.path.exists(path):
        return
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    def euros(x):  # the cash taxes of an award ticket; empty when the collector could not read them
        try:
            return float(x) if x not in (None, "") else None
        except ValueError:
            return None

    def num(x):  # a file that went through pandas or Excel writes 9032.0
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return None

    for fetched in {r.get("fetched") for r in rows} - {r[0] for r in db.execute("SELECT DISTINCT fetched FROM award_miles")}:
        db.executemany("INSERT INTO award_miles(day, orig, dest, cabin, miles, nonstop, fetched, taxes) VALUES(?,?,?,?,?,?,?,?)",
                       [(r["day"], r["orig"], r["dest"], r["cabin"], num(r["miles"]), num(r["nonstop"]), fetched, euros(r.get("taxes")))
                        for r in rows if r.get("fetched") == fetched and num(r.get("nonstop")) is not None])  # bad rows are skipped


DAILY_HOTEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "daily_hotel.csv")


def load_daily_hotel(db, path=None):
    """Load the hotel prices the daily GitHub job saved, once per file version."""
    path = path or DAILY_HOTEL
    if not os.path.exists(path):
        return
    with open(path) as f:
        rows = list(csv.DictReader(f))
    seen = {r[0] for t in ("hotel", "booking", "heat") for r in db.execute(f"SELECT DISTINCT fetched FROM {t}")}
    for fetched in {r["fetched"] for r in rows} - seen:
        got = [r for r in rows if r["fetched"] == fetched]
        price = lambda r: float(r["price"]) if r["price"] else None
        db.executemany("INSERT INTO hotel VALUES(?,?,?,?,?,?,?,?)", [(r["day"], r["query"], r["filt"], int(r["nights"]),
                       r["hotel"] or None, price(r), fetched, int(r["adults"])) for r in got if r["source"] == "google"])
        db.executemany("INSERT INTO booking VALUES(?,?,?,?,?)", [(r["hkey"], r["day"], int(r["nights"]), price(r), fetched)
                                                                 for r in got if r["source"] == "booking"])
        db.executemany("INSERT INTO heat VALUES(?,?,?,?)", [(r["hkey"], r["day"], r["tier"], fetched)
                                                            for r in got if r["source"] == "heat"])
        lookups = [r for r in got if r["source"] != "heat"]
        priced = sum(bool(r["price"]) for r in lookups)
        db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)", ("daily", "Daily: hotel", fetched, None, len(lookups), priced,
                                                                 len(lookups) - priced, 0, None))


def connect(path="tracker.db"):
    db = sqlite3.connect(path, check_same_thread=False)
    db.executescript(SCHEMA)
    try:  # databases created before `adults` existed
        db.execute("ALTER TABLE hotel ADD COLUMN adults INT DEFAULT 2")
    except sqlite3.OperationalError:
        pass
    try:  # databases made before award taxes were stored
        db.execute("ALTER TABLE award_miles ADD COLUMN taxes REAL")
    except sqlite3.OperationalError:
        pass
    try:  # databases from the fast-flights era: its prices were unreliable, so start clean
        db.execute("ALTER TABLE prices ADD COLUMN nonstop INT DEFAULT 1")
        db.execute("DELETE FROM prices")
    except sqlite3.OperationalError:
        pass
    load_daily(db)
    load_daily_hotel(db)
    load_daily_award(db)
    load_daily(db, ROUTES, "Fetched")
    # the repository's newest copies, so data committed since this deploy shows without Streamlit redeploying (loading is idempotent)
    for name, load in (("daily.csv", load_daily), ("daily_hotel.csv", load_daily_hotel), ("daily_award.csv", load_daily_award),
                       ("routes.csv", lambda d, p: load_daily(d, p, "Fetched"))):
        if path := gh.data_file(f"data/{name}"):
            load(db, path)
    for t in ("prices", "hotel", "heat", "booking"):
        db.execute(f"DELETE FROM {t} WHERE fetched < ?", (cutoff(),))
    # award miles live as long as the flight day is ahead: they come from the repo's CSV, so the 5-day fetch retention would make
    # them vanish from the deployed app whenever the owner's computer skips a few days
    db.execute("DELETE FROM award_miles WHERE day <= ?", (datetime.now().date().isoformat(),))
    db.execute("DELETE FROM runs WHERE started < ?", ((datetime.now() - timedelta(days=30)).isoformat(),))  # stats: 30 days
    db.commit()
    return db


if __name__ == "__main__":  # self-check
    import os, tempfile
    from datetime import datetime as dt
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    d = connect(p)
    old, new = (dt.now() - timedelta(days=6)).isoformat(), dt.now().isoformat()
    for f in (old, new):
        d.execute("INSERT INTO prices(day, orig, dest, cabin, price, airlines, fetched) VALUES('2026-11-01','ATH','FRA','economy',100,'Aegean',?)", (f,))
    d.commit()
    assert [r[0] for r in connect(p).execute("SELECT fetched FROM prices")] == [new]
    print("ok")
