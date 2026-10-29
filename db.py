"""Shared SQLite store. Rows older than RETENTION_DAYS are deleted on every connect; fresh rows are reused instead of re-fetched."""
import os
import sqlite3
from datetime import datetime, timedelta

RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", 5))  # raise it when running on your own computer

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices(day TEXT, orig TEXT, dest TEXT, cabin TEXT, price REAL, airlines TEXT, fetched TEXT, nonstop INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS awards(day TEXT, orig TEXT, dest TEXT, miles INT, taxes REAL, PRIMARY KEY(day, orig, dest));
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


def connect(path="tracker.db"):
    db = sqlite3.connect(path, check_same_thread=False)
    db.executescript(SCHEMA)
    try:  # databases created before `adults` existed
        db.execute("ALTER TABLE hotel ADD COLUMN adults INT DEFAULT 2")
    except sqlite3.OperationalError:
        pass
    try:  # databases from the fast-flights era: its prices were unreliable, so start clean
        db.execute("ALTER TABLE prices ADD COLUMN nonstop INT DEFAULT 1")
        db.execute("DELETE FROM prices")
    except sqlite3.OperationalError:
        pass
    for t in ("prices", "hotel", "heat", "booking"):
        db.execute(f"DELETE FROM {t} WHERE fetched < ?", (cutoff(),))
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
